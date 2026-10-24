"""Forecast and model-result contracts, separate from observed market prices.

All amounts are explicit reporting-currency major units. No default financial
assumptions, growth projections, discounting or rounding are performed here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.fundamentals import (
    FlowPeriod,
    FundamentalReportSet,
    PeriodKind,
    QualityIssue,
    QualityStatus,
    ReportIdentity,
    _tuple,
)
from portfolio_dashboard.models import QuoteUnit, _aware, _decimal, _text


def _number(value: Decimal | None, name: str, *, nonnegative: bool = False) -> None:
    if value is not None:
        _decimal(value, name)
        if nonnegative and value < 0:
            raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class InputReference:
    """Exact instrument, class, issuer, report and amended-input selection."""

    instrument_id: str
    share_class_id: str
    report: ReportIdentity
    input_version: str

    def __post_init__(self) -> None:
        for name in ("instrument_id", "share_class_id", "input_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.report, ReportIdentity):
            raise TypeError("report must be ReportIdentity")

    @classmethod
    def from_report_set(cls, report_set: FundamentalReportSet) -> "InputReference":
        if not isinstance(report_set, FundamentalReportSet):
            raise TypeError("report_set must be FundamentalReportSet")
        return cls(
            report_set.identity.listing.instrument.instrument_id,
            report_set.identity.share_class_id,
            report_set.report,
            report_set.input_version,
        )


class Scenario(Enum):
    CAUTIOUS = "cautious"
    BASE = "base"
    OPTIMISTIC = "optimistic"


@dataclass(frozen=True, slots=True)
class ForecastYear:
    year: int
    period: FlowPeriod
    ebit: Decimal | None
    operating_tax_rate: Decimal | None
    depreciation_amortization: Decimal | None
    capex: Decimal | None
    change_operating_nwc: Decimal | None

    def __post_init__(self) -> None:
        if type(self.year) is not int or not 1 <= self.year <= 5:
            raise ValueError("forecast year must be 1..5")
        if not isinstance(self.period, FlowPeriod):
            raise TypeError("period must be FlowPeriod")
        if self.period.kind is not PeriodKind.FISCAL_YEAR or not self.period.complete:
            raise ValueError("forecast requires a complete annual period")
        for name in (
            "ebit",
            "operating_tax_rate",
            "depreciation_amortization",
            "capex",
            "change_operating_nwc",
        ):
            _number(
                getattr(self, name),
                name,
                nonnegative=name in {"depreciation_amortization", "capex"},
            )
        if self.operating_tax_rate is not None and not 0 <= self.operating_tax_rate < 1:
            raise ValueError("operating tax rate must be in [0, 1)")

    @property
    def complete(self) -> bool:
        return all(
            getattr(self, name) is not None
            for name in (
                "ebit",
                "operating_tax_rate",
                "depreciation_amortization",
                "capex",
                "change_operating_nwc",
            )
        )


@dataclass(frozen=True, slots=True)
class TerminalPlan:
    nopat: Decimal | None
    depreciation_amortization: Decimal | None
    capex: Decimal | None
    change_operating_nwc: Decimal | None
    rationale: str

    def __post_init__(self) -> None:
        for name in (
            "nopat",
            "depreciation_amortization",
            "capex",
            "change_operating_nwc",
        ):
            _number(
                getattr(self, name),
                name,
                nonnegative=name in {"depreciation_amortization", "capex"},
            )
        _text(self.rationale, "rationale")

    @property
    def complete(self) -> bool:
        return all(
            getattr(self, name) is not None
            for name in (
                "nopat",
                "depreciation_amortization",
                "capex",
                "change_operating_nwc",
            )
        )


@dataclass(frozen=True, slots=True)
class ScenarioAssumptions:
    inputs: InputReference
    version: str
    scenario: Scenario
    valuation_at: datetime
    currency: str
    years: tuple[ForecastYear, ...]
    terminal: TerminalPlan | None
    wacc: Decimal | None
    terminal_growth: Decimal | None
    sustainable_roic: Decimal | None
    safety_margin: Decimal | None
    rationale: str
    confirmed_at: datetime | None
    confirmation_reference: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, InputReference):
            raise TypeError("inputs must be InputReference")
        _text(self.version, "version")
        if not isinstance(self.scenario, Scenario):
            raise TypeError("scenario must be Scenario")
        _aware(self.valuation_at, "valuation_at")
        QuoteUnit(self.currency, Decimal(1))
        _tuple(self.years, ForecastYear, "years")
        if tuple(y.year for y in self.years) != tuple(range(1, len(self.years) + 1)):
            raise ValueError("forecast years must be ordered, unique and contiguous")
        for year in self.years:
            if year.period.start < self.valuation_at.astimezone(timezone.utc).date():
                raise ValueError("forecast cannot be observed history before t=0")
        for previous, following in zip(self.years, self.years[1:]):
            if (following.period.start - previous.period.end).days != 1:
                raise ValueError("forecast periods must be consecutive")
        if self.terminal is not None and not isinstance(self.terminal, TerminalPlan):
            raise TypeError("terminal must be TerminalPlan or None")
        for name in ("wacc", "terminal_growth", "sustainable_roic", "safety_margin"):
            _number(getattr(self, name), name, nonnegative=True)
        if self.wacc is not None and self.wacc <= 0:
            raise ValueError("WACC must be positive")
        if self.sustainable_roic is not None and self.sustainable_roic <= 0:
            raise ValueError("sustainable ROIC must be positive")
        if self.terminal_growth is not None:
            if self.wacc is not None and self.terminal_growth >= self.wacc:
                raise ValueError("terminal growth must be below WACC")
            if self.sustainable_roic is not None and (
                self.terminal_growth >= self.sustainable_roic
            ):
                raise ValueError("terminal growth must be below sustainable ROIC")
        if self.safety_margin is not None and self.safety_margin >= 1:
            raise ValueError("safety margin must be in [0, 1)")
        _text(self.rationale, "rationale")
        if (self.confirmed_at is None) != (self.confirmation_reference is None):
            raise ValueError("confirmation requires both time and reference")
        if self.confirmed_at is not None:
            _aware(self.confirmed_at, "confirmed_at")
            _text(self.confirmation_reference, "confirmation_reference")
            if self.confirmed_at > self.valuation_at:
                raise ValueError("confirmation must be available at valuation time")

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        issues = []
        if self.confirmed_at is None:
            issues.append(
                QualityIssue(
                    QualityStatus.UNCONFIRMED_ASSUMPTIONS, "scenario_confirmation"
                )
            )
        if (
            len(self.years) != 5
            or not all(y.complete for y in self.years)
            or self.terminal is None
            or not self.terminal.complete
            or any(
                v is None
                for v in (self.wacc, self.terminal_growth, self.sustainable_roic)
            )
        ):
            issues.append(QualityIssue(QualityStatus.INCOMPLETE, "scenario_components"))
        return tuple(issues)

    def issues_for(self, report_set: FundamentalReportSet) -> tuple[QualityIssue, ...]:
        """Retain input issues and reject reuse after a new report/input version."""
        if not isinstance(report_set, FundamentalReportSet):
            raise TypeError("report_set must be FundamentalReportSet")
        issues = self.issues + report_set.issues_at(self.valuation_at)
        if (
            self.inputs != InputReference.from_report_set(report_set)
            or self.currency != report_set.currency
        ):
            return issues + (
                QualityIssue(QualityStatus.INVALID, "assumptions_input_mismatch"),
            )
        return issues


@dataclass(frozen=True, slots=True)
class CalculationStep:
    """Unrounded finite Decimal output with an explicit unit and formula label."""

    name: str
    formula: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        for name in ("name", "formula", "unit"):
            _text(getattr(self, name), name)
        _decimal(self.value, "value")


@dataclass(frozen=True, slots=True)
class IntrinsicValueResult:
    """Model output, never models.Valuation or a portfolio position value.

    The calculator supplies all input and assumption issues and validates the
    financial equations. Blocking results retain evidence/steps, without a price.
    """

    inputs: InputReference
    assumptions_version: str
    scenario: Scenario
    model_version: str
    valuation_at: datetime
    currency: str
    issues: tuple[QualityIssue, ...]
    source_references: tuple[str, ...]
    steps: tuple[CalculationStep, ...]
    value_per_share: Decimal | None
    target_price: Decimal | None
    terminal_value_share: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, InputReference):
            raise TypeError("inputs must be InputReference")
        for name in ("assumptions_version", "model_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.scenario, Scenario):
            raise TypeError("scenario must be Scenario")
        _aware(self.valuation_at, "valuation_at")
        QuoteUnit(self.currency, Decimal(1))
        _tuple(self.issues, QualityIssue, "issues")
        _tuple(self.steps, CalculationStep, "steps")
        _tuple(self.source_references, str, "source_references")
        for reference in self.source_references:
            _text(reference, "source_reference")
        for name in ("value_per_share", "target_price", "terminal_value_share"):
            _number(getattr(self, name), name, nonnegative=True)
        if self.value_per_share is not None and self.value_per_share <= 0:
            raise ValueError("available intrinsic value must be positive")
        if any(issue.blocking for issue in self.issues):
            if self.value_per_share is not None or self.target_price is not None:
                raise ValueError("blocking result cannot expose a model price")
        elif self.value_per_share is None:
            raise ValueError("unavailable result must explain its blocking reason")
        elif (
            not self.steps
            or not self.source_references
            or self.terminal_value_share is None
        ):
            raise ValueError("available result requires evidence and calculation steps")
        if self.target_price is not None and (
            self.value_per_share is None or self.target_price > self.value_per_share
        ):
            raise ValueError("target price requires a compatible model value")
