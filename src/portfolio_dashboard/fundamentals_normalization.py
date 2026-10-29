"""Pure normalization of provider statements into the M8-03 fundamental contracts.

M8-04 (#73). The input is plain, provider-neutral data (``ProviderStatements``:
row label -> period end -> ``Decimal | None``); the output reuses the existing
``FundamentalObservation``/``FundamentalReportSet`` contracts. Nothing here
imports pandas, a provider, Streamlit or the network, reads the clock, or knows
a real symbol: the retrieval time and the owner-confirmed expectations are
explicit arguments. The adapter that fills ``ProviderStatements`` lives in
``fundamentals_provider``.

Rules that keep the numbers honest (docs/FUNDAMENTALS_PROVIDER.md):

- An absent row, a NaN cell or an unparsable cell is *missing*, never zero. An
  explicitly delivered zero is a delivered value.
- One provider row is chosen per field for the whole series, never mixed per
  year. The preference lists below are fixed and reviewed (#71).
- A currency, identity or scale conflict withholds every number
  (``numbers_withheld``); there is no EUR or zero fallback.
- Fields without a provider source (operating tax rate, excess cash, ...) are
  emitted as *missing* observations for the manual supplement path; a provider
  figure appears only as a clearly labelled ``Suggestion``.
- Only annual fiscal-year flows are produced. Quarterly and cumulative periods
  never enter the report set; ``trailing_twelve_months`` is the only place that
  combines quarters, and only complete, contiguous, non-overlapping ones.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum

from portfolio_dashboard.fundamentals import (
    CapitalStructure,
    CurrentSharesPeriod,
    EvidenceKind,
    FieldProvenance,
    FlowPeriod,
    FundamentalField,
    FundamentalObservation,
    FundamentalReportSet,
    FundamentalUnit,
    InstantPeriod,
    PeriodKind,
    QualityIssue,
    QualityStatus,
    ReportIdentity,
    UnitKind,
    VerifiedFundamentalIdentity,
    WeightedSharesPeriod,
)
from portfolio_dashboard.models import QuoteUnit, ValueStatus, _aware, _decimal, _text

PROVIDER = "yfinance"

# Fixed, reviewed provider row labels per contract field, in order of preference.
EBIT_ROWS = ("Operating Income", "Total Operating Income As Reported")
EBIT_CONTROL_ROWS = ("EBIT",)  # Other definition (includes non-operating items).
PRETAX_ROWS = ("Pretax Income",)
TAX_ROWS = ("Tax Provision",)
DA_ROWS = ("Depreciation And Amortization", "Depreciation Amortization Depletion")
CAPEX_ROWS = ("Capital Expenditure", "Purchase Of PPE")
OPERATING_CASH_ROWS = ("Operating Cash Flow",)
FREE_CASH_ROWS = ("Free Cash Flow",)
CURRENT_ASSET_ROWS = ("Current Assets",)
CURRENT_LIABILITY_ROWS = ("Current Liabilities",)
CASH_ROWS = (
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Cash Equivalents",
)
CURRENT_DEBT_ROWS = ("Current Debt And Capital Lease Obligation", "Current Debt")
DEBT_ROWS = ("Total Debt",)
LEASE_ROWS = ("Capital Lease Obligations",)  # Subset of total debt, if shown.
MINORITY_ROWS = ("Minority Interest",)
PREFERRED_ROWS = (
    "Preferred Stock Equity",
    "Preferred Securities Outside Stock Equity",
    "Preferred Stock",
)
BALANCE_SHARE_ROWS = ("Ordinary Shares Number",)
NET_INCOME_ROWS = (
    "Diluted NI Availto Com Stockholders",
    "Net Income Common Stockholders",
)
EPS_SHARE_ROWS = ("Diluted Average Shares",)
EPS_ROWS = ("Diluted EPS",)

INFO_KEYS = (
    "quoteType",
    "currency",
    "financialCurrency",
    "sharesOutstanding",
    "impliedSharesOutstanding",
)

PERIOD_END_TOLERANCE_DAYS = 10  # Requirement 8 of docs/FUNDAMENTALS_COVERAGE.md.
MIN_ANNUAL_DAYS = 364  # Same bounds as a complete FlowPeriod fiscal year.
MAX_ANNUAL_DAYS = 371
MIN_QUARTER_DAYS = 84
MAX_QUARTER_DAYS = 98
SHARE_CONFLICT_RATIO = Decimal("0.10")
EPS_TOLERANCE = Decimal("0.05")
EBIT_CONTROL_TOLERANCE = Decimal("0.01")
SPLIT_BASIS_UNVERIFIED = "provider-unverified"
NOT_SUPPLIED = "not_supplied_by_provider"
REVISION_UNKNOWN = "provider-revision-unknown"

# Sanitized failure classes. Messages, symbols and raw responses never appear.
FAILURE_TIMEOUT = "timeout"
FAILURE_ACCESS_RESTRICTED = "access_restricted"
FAILURE_PROVIDER_ERROR = "provider_error"
FAILURE_EMPTY = "empty"
FAILURE_UNEXPECTED_SCHEMA = "unexpected_schema"
FAILURE_INVALID_VALUES = "invalid_values"
FAILURE_METADATA_PLACEHOLDER = "metadata_placeholder"
FAILURE_IDENTITY_MISMATCH = "identity_mismatch"
FAILURE_UNSUPPORTED_PROVIDER = "unsupported_provider"
FAILURE_IDENTITY_NOT_SUPPORTED = "identity_not_supported"
FAILURE_CODES = frozenset(
    {
        FAILURE_TIMEOUT,
        FAILURE_ACCESS_RESTRICTED,
        FAILURE_PROVIDER_ERROR,
        FAILURE_EMPTY,
        FAILURE_UNEXPECTED_SCHEMA,
        FAILURE_INVALID_VALUES,
        FAILURE_METADATA_PLACEHOLDER,
        FAILURE_IDENTITY_MISMATCH,
        FAILURE_UNSUPPORTED_PROVIDER,
        FAILURE_IDENTITY_NOT_SUPPORTED,
    }
)
DATASETS = frozenset(
    {
        "request",
        "annual_income",
        "annual_balance",
        "annual_cashflow",
        "info",
        "shares_full",
    }
)


class FieldOrigin(Enum):
    PROVIDER = "provider"  # Delivered automatically; a gap becomes a manual item.
    CONTROL = "control"  # Provider control value only; never a model input.
    MANUAL = "manual"  # No automatic value; always a manual supplement.
    DERIVED = "derived"  # Computed by the calculator from other fields.


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    field: FundamentalField
    origin: FieldOrigin
    provider_rows: tuple[str, ...]
    treatment: str


FIELD_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        FundamentalField.EBIT,
        FieldOrigin.PROVIDER,
        EBIT_ROWS,
        "Preference list; EBIT row only a control. Owner confirms the definition.",
    ),
    CatalogEntry(
        FundamentalField.OPERATING_TAX_RATE,
        FieldOrigin.MANUAL,
        TAX_ROWS + PRETAX_ROWS,
        "Provider yields only the effective rate; shown as a suggestion.",
    ),
    CatalogEntry(
        FundamentalField.DEPRECIATION_AMORTIZATION,
        FieldOrigin.PROVIDER,
        DA_ROWS,
        "Preference list; negative values are invalid.",
    ),
    CatalogEntry(
        FundamentalField.CAPEX,
        FieldOrigin.PROVIDER,
        CAPEX_ROWS,
        "Provider outflow is negative; sign reversed to a positive deduction.",
    ),
    CatalogEntry(
        FundamentalField.OPERATING_NWC,
        FieldOrigin.PROVIDER,
        CURRENT_ASSET_ROWS + CASH_ROWS + CURRENT_LIABILITY_ROWS + CURRENT_DEBT_ROWS,
        "(Current assets - cash) - (current liabilities - current debt) per balance "
        "date; one missing component makes that date missing.",
    ),
    CatalogEntry(
        FundamentalField.CHANGE_OPERATING_NWC,
        FieldOrigin.DERIVED,
        (),
        "Closing minus opening NWC, derived by the calculator.",
    ),
    CatalogEntry(
        FundamentalField.PROVIDER_FCF,
        FieldOrigin.CONTROL,
        FREE_CASH_ROWS,
        "Control value only; never FCFF.",
    ),
    CatalogEntry(
        FundamentalField.EXCESS_CASH,
        FieldOrigin.MANUAL,
        CASH_ROWS,
        "Gross cash is only an upper-bound suggestion.",
    ),
    CatalogEntry(
        FundamentalField.NONOPERATING_ASSETS,
        FieldOrigin.MANUAL,
        (),
        "No provider source.",
    ),
    CatalogEntry(
        FundamentalField.FINANCIAL_DEBT,
        FieldOrigin.PROVIDER,
        DEBT_ROWS,
        "Total debt including leases, as reported; owner confirms the lease treatment "
        "matches EBIT (no double charge).",
    ),
    CatalogEntry(
        FundamentalField.PREFERRED_CLAIMS,
        FieldOrigin.MANUAL,
        PREFERRED_ROWS,
        "A provider book value is only a suggestion; a missing row is never zero.",
    ),
    CatalogEntry(
        FundamentalField.MINORITY_CLAIMS,
        FieldOrigin.MANUAL,
        MINORITY_ROWS,
        "A provider book value is only a suggestion; a missing row is never zero.",
    ),
    CatalogEntry(
        FundamentalField.OUTSTANDING_SHARES,
        FieldOrigin.PROVIDER,
        ("shares_full", "sharesOutstanding", "impliedSharesOutstanding")
        + BALANCE_SHARE_ROWS,
        "Dated series only; undated counts are a suggestion; sources > 10% apart block.",
    ),
    CatalogEntry(
        FundamentalField.INCREMENTAL_DILUTION,
        FieldOrigin.MANUAL,
        (),
        "No provider source.",
    ),
    CatalogEntry(
        FundamentalField.WEIGHTED_EPS_SHARES,
        FieldOrigin.CONTROL,
        EPS_SHARE_ROWS,
        "Historical weighted count; control value only.",
    ),
)
MANUAL_FIELDS = frozenset(
    e.field for e in FIELD_CATALOG if e.origin is FieldOrigin.MANUAL
)


@dataclass(frozen=True, slots=True)
class FetchFailure:
    dataset: str
    code: str

    def __post_init__(self) -> None:
        if self.dataset not in DATASETS:
            raise ValueError("unknown dataset")
        if self.code not in FAILURE_CODES:
            raise ValueError("unknown failure code")


def _cell(value: object) -> None:
    if value is not None:
        _decimal(value, "cell")


@dataclass(frozen=True, slots=True)
class StatementTable:
    """Row label -> period end -> finite Decimal, or None for a missing cell."""

    rows: Mapping[str, Mapping[date, Decimal | None]]

    def __post_init__(self) -> None:
        if not isinstance(self.rows, Mapping):
            raise TypeError("rows must be a mapping")
        for label, cells in self.rows.items():
            _text(label, "row label")
            if not isinstance(cells, Mapping):
                raise TypeError("row cells must be a mapping")
            for end, value in cells.items():
                if type(end) is not date:
                    raise TypeError("period ends must be dates")
                _cell(value)

    def value(self, label: str, end: date) -> Decimal | None:
        return self.rows.get(label, {}).get(end)

    def period_ends(self) -> tuple[date, ...]:
        """Ends with at least one value, newest first; empty columns are dropped."""
        ends = {
            end
            for cells in self.rows.values()
            for end, value in cells.items()
            if value is not None
        }
        return tuple(sorted(ends, reverse=True))


@dataclass(frozen=True, slots=True)
class ProviderStatements:
    """One retrieval of annual statements and metadata; None means unavailable."""

    retrieved_at: datetime
    annual_income: StatementTable | None = None
    annual_balance: StatementTable | None = None
    annual_cashflow: StatementTable | None = None
    info: Mapping[str, object] | None = None
    dated_shares: tuple[tuple[date, Decimal], ...] | None = None
    failures: tuple[FetchFailure, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.retrieved_at, "retrieved_at")
        for name in ("annual_income", "annual_balance", "annual_cashflow"):
            table = getattr(self, name)
            if table is not None and not isinstance(table, StatementTable):
                raise TypeError(f"{name} must be StatementTable or None")
        if self.info is not None and not isinstance(self.info, Mapping):
            raise TypeError("info must be a mapping or None")
        if self.dated_shares is not None:
            for entry in self.dated_shares:
                if (
                    not isinstance(entry, tuple)
                    or len(entry) != 2
                    or type(entry[0]) is not date
                ):
                    raise TypeError("dated_shares must be (date, Decimal) tuples")
                _decimal(entry[1], "share count")
        if not isinstance(self.failures, tuple) or not all(
            isinstance(f, FetchFailure) for f in self.failures
        ):
            raise TypeError("failures must be a tuple of FetchFailure")


@dataclass(frozen=True, slots=True)
class ProviderExpectation:
    """Owner-confirmed expectations the provider's answer is checked against."""

    reporting_currency: str
    fiscal_year_end_month: int
    fiscal_year_end_day: int

    def __post_init__(self) -> None:
        QuoteUnit(self.reporting_currency, Decimal(1))
        date(2000, self.fiscal_year_end_month, self.fiscal_year_end_day)


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Provider figure offered for a manual field; it is never an observation."""

    field: FundamentalField
    period: FlowPeriod | InstantPeriod | CurrentSharesPeriod | None
    value: Decimal
    basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.field, FundamentalField):
            raise TypeError("field must be FundamentalField")
        _decimal(self.value, "value")
        _text(self.basis, "basis")


@dataclass(frozen=True, slots=True)
class NormalizedFundamentals:
    identity: VerifiedFundamentalIdentity
    report: ReportIdentity
    currency: str | None
    observations: tuple[FundamentalObservation, ...]
    suggestions: tuple[Suggestion, ...]
    issues: tuple[QualityIssue, ...]  # Dataset-, identity- and cross-check level.
    notes: tuple[str, ...]  # Advisory codes that do not block.
    bridge_date: date | None
    shares_period: CurrentSharesPeriod | None
    numbers_withheld: bool  # An identity/currency/scale conflict removed all numbers.
    retrieved_at: datetime

    @property
    def pending(self) -> tuple[FundamentalObservation, ...]:
        """Observations without a usable number: gaps and fields to supplement."""
        return tuple(
            o for o in self.observations if o.status is not ValueStatus.AVAILABLE
        )

    @property
    def manual_required(self) -> tuple[FundamentalObservation, ...]:
        """Fields that have no automatic value and need a confirmed manual one."""
        return tuple(o for o in self.pending if o.field in MANUAL_FIELDS)


# --- primitives ---------------------------------------------------------------


def _number(value: object) -> Decimal | None:
    """Finite positive-or-negative number from an info value; bools/strings -> None."""
    if value is None or isinstance(value, (bool, str)):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _aligned_end(ends: Sequence[date], target: date) -> date | None:
    """Table end within the tolerance of ``target``; exact match wins."""
    near = [e for e in ends if abs((e - target).days) <= PERIOD_END_TOLERANCE_DAYS]
    if not near:
        return None
    return min(near, key=lambda e: (abs((e - target).days), e))


def _pick_row(
    table: StatementTable | None,
    labels: Sequence[str],
    columns: Sequence[date],
) -> str | None:
    """One row for the whole series: the first with all columns, else the fullest."""
    if table is None or not columns:
        return None
    best, best_count = None, 0
    for label in labels:
        count = sum(table.value(label, c) is not None for c in columns)
        if count == len(columns):
            return label
        if count > best_count:
            best, best_count = label, count
    return best


def _fiscal_year_end_matches(end: date, expectation: ProviderExpectation) -> bool:
    month, day = expectation.fiscal_year_end_month, expectation.fiscal_year_end_day
    for year in (end.year - 1, end.year, end.year + 1):
        try:
            anchor = date(year, month, day)
        except ValueError:  # 29 February in a non-leap year.
            anchor = date(year, month, 28)
        if abs((end - anchor).days) <= PERIOD_END_TOLERANCE_DAYS:
            return True
    return False


_MINOR_UNIT_TOKENS = {
    ("GBP", Decimal("0.01")): "GBp",
    ("ZAR", Decimal("0.01")): "ZAc",
    ("ILS", Decimal("0.01")): "ILA",
}


def _quote_token_matches(token: object, unit: QuoteUnit) -> bool:
    if unit.scale_to_currency == Decimal(1):
        return token == unit.currency
    return _MINOR_UNIT_TOKENS.get((unit.currency, unit.scale_to_currency)) == token


def _issue(status: QualityStatus, code: str) -> QualityIssue:
    return QualityIssue(status, code)


_FAILURE_STATUS = {
    FAILURE_INVALID_VALUES: QualityStatus.INVALID,
    FAILURE_IDENTITY_MISMATCH: QualityStatus.INVALID,
    FAILURE_IDENTITY_NOT_SUPPORTED: QualityStatus.UNSUPPORTED_MODEL,
}


class _Normalizer:
    def __init__(
        self,
        identity: VerifiedFundamentalIdentity,
        statements: ProviderStatements,
        expectation: ProviderExpectation,
    ) -> None:
        self.identity = identity
        self.s = statements
        self.expectation = expectation
        self.currency = expectation.reporting_currency
        stamp = statements.retrieved_at.astimezone(timezone.utc)
        self.retrieved_on = stamp.date()
        self.report = ReportIdentity(
            identity.issuer_id,
            f"{PROVIDER}-fetch-{stamp:%Y%m%dT%H%M%SZ}",
            REVISION_UNKNOWN,
            identity.consolidation_scope,
        )
        self.observations: list[FundamentalObservation] = []
        self.suggestions: list[Suggestion] = []
        self.issues: list[QualityIssue] = []
        self.notes: list[str] = []
        self.fallback_rows: set[str] = set()

    def _pick(self, table, labels, columns) -> str | None:
        """Choose one series row; a row beyond the first preference is flagged."""
        row = _pick_row(table, labels, columns)
        if row is not None and row != labels[0]:
            self.fallback_rows.add(row)
            self.notes.append("fallback_row_" + labels[0].lower().replace(" ", "_"))
        return row

    # -- observation factories ---------------------------------------------

    def _unit(self, kind: UnitKind) -> FundamentalUnit:
        if kind is UnitKind.MONEY:
            return FundamentalUnit(kind, Decimal(1), self.currency)
        return FundamentalUnit(kind, Decimal(1))

    def _provenance(
        self, statement: str, row: str | None, normalization: str
    ) -> FieldProvenance:
        definition = (
            f"{PROVIDER} {statement} row '{row}'"
            if row
            else f"{PROVIDER} {statement}: no source row"
        )
        return FieldProvenance(
            self.identity.source,
            EvidenceKind.PROVIDER,
            row or NOT_SUPPLIED,
            definition,
            normalization,
            self.s.retrieved_at,
        )

    def add(
        self,
        field: FundamentalField,
        value: Decimal | None,
        kind: UnitKind,
        period,
        statement: str,
        row: str | None,
        normalization: str = "unchanged; absolute provider units",
        *,
        invalid: bool = False,
    ) -> FundamentalObservation:
        """Append an observation; a value the field rule rejects becomes invalid."""
        if row and self.fallback_rows & set(row.split("+")):
            normalization += "; fallback row, definition may differ from preferred"
        provenance = self._provenance(statement, row, normalization)
        unit = self._unit(kind)
        status = ValueStatus.AVAILABLE if value is not None else ValueStatus.MISSING
        if invalid:
            status, value = ValueStatus.INVALID, None
        try:
            obs = FundamentalObservation(
                field, value, status, unit, period, self.report, provenance
            )
        except ValueError:
            obs = FundamentalObservation(
                field, None, ValueStatus.INVALID, unit, period, self.report, provenance
            )
        self.observations.append(obs)
        return obs

    # -- stages ------------------------------------------------------------

    def run(self) -> NormalizedFundamentals:
        self._failures()
        if not self._metadata_ok() or not self._scale_ok():
            return self._result((), None, None, withheld=True)
        periods, opening = self._annual_periods()
        shares_period = self._shares(periods)
        bridge_date = periods[0].end if periods else None
        if periods:
            self._flows(periods)
            self._working_capital(periods, opening)
            self._bridge(periods[0])
        return self._result(
            tuple(self.observations), bridge_date, shares_period, withheld=False
        )

    def _result(self, observations, bridge_date, shares_period, *, withheld):
        return NormalizedFundamentals(
            self.identity,
            self.report,
            None if withheld else self.currency,
            observations,
            () if withheld else tuple(self.suggestions),
            tuple(dict.fromkeys(self.issues)),
            tuple(dict.fromkeys(self.notes)),
            bridge_date,
            shares_period,
            withheld,
            self.s.retrieved_at,
        )

    def _failures(self) -> None:
        for failure in self.s.failures:
            status = _FAILURE_STATUS.get(failure.code, QualityStatus.MISSING)
            self.issues.append(_issue(status, f"{failure.dataset}_{failure.code}"))

    def _metadata_ok(self) -> bool:
        """Identity and currency checks; any failure withholds all numbers."""
        info = self.s.info
        if not info or not (info.get("quoteType") or info.get("currency")):
            # A throttled stub has neither type nor currency: absence, not mismatch.
            if info:
                self.issues.append(
                    _issue(QualityStatus.MISSING, "info_metadata_placeholder")
                )
            self.issues.append(
                _issue(QualityStatus.INCOMPLETE, "reporting_currency_unverified")
            )
            return False
        ok = True
        if info.get("quoteType") != "EQUITY":
            self.issues.append(_issue(QualityStatus.INVALID, "quote_type_conflict"))
            ok = False
        unit = self.identity.listing.instrument.quote_unit
        if unit is None:
            self.notes.append("trading_currency_not_checked")
        elif not _quote_token_matches(info.get("currency"), unit):
            self.issues.append(
                _issue(QualityStatus.INVALID, "trading_currency_conflict")
            )
            ok = False
        reported = info.get("financialCurrency")
        if not isinstance(reported, str) or not reported.strip():
            self.issues.append(
                _issue(QualityStatus.INCOMPLETE, "reporting_currency_unverified")
            )
            ok = False
        elif reported != self.currency:
            self.issues.append(
                _issue(QualityStatus.INVALID, "reporting_currency_conflict")
            )
            ok = False
        elif reported != info.get("currency"):
            self.notes.append("reporting_currency_differs_from_trading_currency")
        return ok and not any(
            f.code == FAILURE_IDENTITY_MISMATCH for f in self.s.failures
        )

    def _scale_ok(self) -> bool:
        """EPS = net income / average diluted shares verifies money/share scale."""
        income = self.s.annual_income
        for end in income.period_ends() if income else ():
            net = next(
                (v for r in NET_INCOME_ROWS if (v := income.value(r, end)) is not None),
                None,
            )
            shares = next(
                (v for r in EPS_SHARE_ROWS if (v := income.value(r, end)) is not None),
                None,
            )
            eps = next(
                (v for r in EPS_ROWS if (v := income.value(r, end)) is not None), None
            )
            if net is None or shares is None or eps is None or shares <= 0 or eps == 0:
                continue
            if abs((net / shares) / eps - 1) > EPS_TOLERANCE:
                self.issues.append(_issue(QualityStatus.INVALID, "scale_inconsistent"))
                return False
            return True
        self.notes.append("scale_unverified")
        return True

    def _annual_periods(self) -> tuple[list[FlowPeriod], date | None]:
        """Newest contiguous chain of fiscal years whose start is evidenced.

        Returns periods newest first and the opening balance date of the oldest.
        """
        income = self.s.annual_income
        ends = [
            e
            for e in (income.period_ends() if income else ())
            if e <= self.retrieved_on
        ]
        if income and len(ends) < len(income.period_ends()):
            self.issues.append(_issue(QualityStatus.INVALID, "future_period"))
        chain: list[date] = []
        for end in ends:
            if not _fiscal_year_end_matches(end, self.expectation):
                self.issues.append(
                    _issue(QualityStatus.INVALID, "fiscal_year_end_mismatch")
                )
                break
            if (
                chain
                and not MIN_ANNUAL_DAYS <= (chain[-1] - end).days <= MAX_ANNUAL_DAYS
            ):
                self.notes.append("annual_history_gap")
                break
            chain.append(end)
        opening: date | None = None
        if chain:
            balance_ends = (
                self.s.annual_balance.period_ends() if self.s.annual_balance else ()
            )
            opening = next(
                (
                    b
                    for b in balance_ends
                    if MIN_ANNUAL_DAYS <= (chain[-1] - b).days <= MAX_ANNUAL_DAYS
                ),
                None,
            )
            if opening is None:
                # Without an evidenced predecessor the oldest year has no start.
                self.notes.append("oldest_period_start_unknown")
                opening = chain.pop()
        periods = []
        for index, end in enumerate(chain):
            previous = chain[index + 1] if index + 1 < len(chain) else opening
            periods.append(
                FlowPeriod(
                    previous + timedelta(days=1),
                    end,
                    PeriodKind.FISCAL_YEAR,
                    f"FY ending {end.isoformat()}",
                    True,
                )
            )
        return periods, opening

    def _flows(self, periods: list[FlowPeriod]) -> None:
        income, cashflow = self.s.annual_income, self.s.annual_cashflow
        ends = [p.end for p in periods]
        cash_ends = cashflow.period_ends() if cashflow else ()
        cash_columns = {e: _aligned_end(cash_ends, e) for e in ends}
        if any(c is not None and c != e for e, c in cash_columns.items()):
            self.notes.append("period_end_aligned")
        used_cash = [c for c in cash_columns.values() if c is not None]

        ebit_row = self._pick(income, EBIT_ROWS, ends)
        da_row = self._pick(cashflow, DA_ROWS, used_cash)
        capex_row = self._pick(cashflow, CAPEX_ROWS, used_cash)
        fcf_row = self._pick(cashflow, FREE_CASH_ROWS, used_cash)
        eps_row = self._pick(income, EPS_SHARE_ROWS, ends)

        for period in periods:
            end = period.end
            cash_end = cash_columns[end]

            ebit = income.value(ebit_row, end) if ebit_row else None
            self.add(
                FundamentalField.EBIT,
                ebit,
                UnitKind.MONEY,
                period,
                "income statement",
                ebit_row or EBIT_ROWS[0],
                "unchanged; definition to be confirmed by the owner",
            )
            self._ebit_control(income, ebit, end)

            self._tax_rate(income, period)

            da = cashflow.value(da_row, cash_end) if da_row and cash_end else None
            self.add(
                FundamentalField.DEPRECIATION_AMORTIZATION,
                da,
                UnitKind.MONEY,
                period,
                "cash flow",
                da_row or DA_ROWS[0],
                invalid=da is not None and da < 0,
            )

            raw = (
                cashflow.value(capex_row, cash_end) if capex_row and cash_end else None
            )
            capex = (
                None if raw is None else -raw
            )  # Outflow is negative at the provider.
            if raw is not None and raw == 0:
                self.notes.append("capex_zero_reported")
            self.add(
                FundamentalField.CAPEX,
                capex,
                UnitKind.MONEY,
                period,
                "cash flow",
                capex_row or CAPEX_ROWS[0],
                "sign reversed: provider outflow is negative, deduction is positive",
                invalid=raw is not None and raw > 0,
            )
            if raw is not None and raw > 0:
                self.notes.append("capex_sign_unexpected")
            if raw is None and cash_end and cashflow:
                operating = cashflow.value(OPERATING_CASH_ROWS[0], cash_end)
                free = cashflow.value(FREE_CASH_ROWS[0], cash_end)
                if operating is not None and operating == free:
                    self.notes.append("provider_fcf_equals_operating_cash_flow")

            if fcf_row and cash_end:
                free = cashflow.value(fcf_row, cash_end)
                if free is not None:
                    self.add(
                        FundamentalField.PROVIDER_FCF,
                        free,
                        UnitKind.MONEY,
                        period,
                        "cash flow",
                        fcf_row,
                        "control value only; not FCFF",
                    )
            weighted = income.value(eps_row, end) if eps_row else None
            if weighted is not None and weighted > 0:
                self.add(
                    FundamentalField.WEIGHTED_EPS_SHARES,
                    weighted,
                    UnitKind.SHARES,
                    WeightedSharesPeriod(period, SPLIT_BASIS_UNVERIFIED),
                    "income statement",
                    eps_row,
                    "control value only; split basis not verified",
                )

    def _ebit_control(self, income, ebit: Decimal | None, end: date) -> None:
        control = income.value(EBIT_CONTROL_ROWS[0], end) if income else None
        if ebit is None or control is None:
            return
        if abs(control - ebit) > EBIT_CONTROL_TOLERANCE * abs(ebit):
            self.notes.append("ebit_control_definition_differs")

    def _tax_rate(self, income, period: FlowPeriod) -> None:
        """The operating rate is manual; the effective rate is only a suggestion."""
        self.add(
            FundamentalField.OPERATING_TAX_RATE,
            None,
            UnitKind.RATE,
            period,
            "income statement",
            None,
            "no provider source for the operating rate",
        )
        tax = income.value(TAX_ROWS[0], period.end)
        pretax = income.value(PRETAX_ROWS[0], period.end)
        if tax is not None and pretax is not None and pretax > 0:
            rate = tax / pretax
            if 0 <= rate < 1:
                self.suggestions.append(
                    Suggestion(
                        FundamentalField.OPERATING_TAX_RATE,
                        period,
                        rate,
                        "effective_rate_tax_provision_over_pretax_income",
                    )
                )

    def _working_capital(self, periods: list[FlowPeriod], opening: date) -> None:
        balance = self.s.annual_balance
        targets = [p.end for p in periods] + [opening]
        ends = balance.period_ends() if balance else ()
        columns = {t: _aligned_end(ends, t) for t in targets}
        if any(c is not None and c != t for t, c in columns.items()):
            self.notes.append("period_end_aligned")
        used = [c for c in columns.values() if c is not None]
        rows = {
            name: self._pick(balance, labels, used)
            for name, labels in (
                ("assets", CURRENT_ASSET_ROWS),
                ("liabilities", CURRENT_LIABILITY_ROWS),
                ("cash", CASH_ROWS),
                ("debt", CURRENT_DEBT_ROWS),
            )
        }
        for target in targets:
            column = columns[target]
            parts = {
                name: balance.value(row, column) if row and column else None
                for name, row in rows.items()
            }
            nwc = None
            if all(v is not None for v in parts.values()):
                nwc = (parts["assets"] - parts["cash"]) - (
                    parts["liabilities"] - parts["debt"]
                )
            self.add(
                FundamentalField.OPERATING_NWC,
                nwc,
                UnitKind.MONEY,
                InstantPeriod(target),
                "balance sheet",
                None if nwc is None else "+".join(r for r in rows.values() if r),
                "(current assets - cash) - (current liabilities - current debt)",
            )

    def _bridge(self, latest: FlowPeriod) -> None:
        balance = self.s.annual_balance
        at = InstantPeriod(latest.end)
        ends = balance.period_ends() if balance else ()
        column = _aligned_end(ends, latest.end)

        def read(labels):
            row = self._pick(balance, labels, [column] if column else [])
            return (row, balance.value(row, column)) if row else (None, None)

        debt_row, debt = read(DEBT_ROWS)
        current_debt = read(CURRENT_DEBT_ROWS)[1]
        below = debt is not None and current_debt is not None and debt < current_debt
        if below:
            self.notes.append("financial_debt_below_current_debt")
        self.add(
            FundamentalField.FINANCIAL_DEBT,
            debt,
            UnitKind.MONEY,
            at,
            "balance sheet",
            debt_row or DEBT_ROWS[0],
            "total debt including leases, as reported",
            invalid=below or (debt is not None and debt < 0),
        )
        lease = read(LEASE_ROWS)[1]
        if lease is None:
            self.notes.append("lease_split_not_visible")
        elif lease > 0:
            self.notes.append("financial_debt_includes_leases")
        cash = read(CASH_ROWS)[1]
        for field, labels, basis in (
            (FundamentalField.EXCESS_CASH, None, None),
            (FundamentalField.NONOPERATING_ASSETS, None, None),
            (FundamentalField.PREFERRED_CLAIMS, PREFERRED_ROWS, "provider_book_value"),
            (FundamentalField.MINORITY_CLAIMS, MINORITY_ROWS, "provider_book_value"),
        ):
            self.add(
                field,
                None,
                UnitKind.MONEY,
                at,
                "balance sheet",
                None,
                "no automatic value; owner supplies and confirms",
            )
            if field is FundamentalField.EXCESS_CASH and cash is not None and cash >= 0:
                self.suggestions.append(
                    Suggestion(field, at, cash, "gross_cash_upper_bound")
                )
            elif labels:
                _, figure = read(labels)
                if figure is not None and figure >= 0:
                    self.suggestions.append(Suggestion(field, at, figure, basis))

    def _shares(self, periods: list[FlowPeriod]) -> CurrentSharesPeriod | None:
        """Current count only with a date; undated counts stay suggestions."""
        info = self.s.info or {}
        dated = [
            (d, v)
            for d, v in (self.s.dated_shares or ())
            if v > 0 and d <= self.retrieved_on
        ]
        latest = max(dated, key=lambda e: e[0]) if dated else None
        sources: dict[str, Decimal] = {}
        for key in ("sharesOutstanding", "impliedSharesOutstanding"):
            value = _number(info.get(key))
            if value is not None and value > 0:
                sources[key] = value
        undated = sources.get("sharesOutstanding") or sources.get(
            "impliedSharesOutstanding"
        )
        if latest is not None:
            sources["shares_full"] = latest[1]
        balance = self.s.annual_balance
        if periods and balance:
            column = _aligned_end(balance.period_ends(), periods[0].end)
            row = self._pick(balance, BALANCE_SHARE_ROWS, [column] if column else [])
            held = balance.value(row, column) if row else None
            if held is not None and held > 0:
                sources["balance_sheet"] = held
        if latest is None:
            self.issues.append(_issue(QualityStatus.INCOMPLETE, "shares_undated"))
            if undated is not None:
                self.suggestions.append(
                    Suggestion(
                        FundamentalField.OUTSTANDING_SHARES,
                        None,
                        undated,
                        "provider_undated_count",
                    )
                )
            return None
        period = CurrentSharesPeriod(latest[0], SPLIT_BASIS_UNVERIFIED)
        conflict = (
            len(sources) > 1
            and max(sources.values()) / min(sources.values()) - 1 > SHARE_CONFLICT_RATIO
        )
        if conflict:
            self.issues.append(_issue(QualityStatus.INVALID, "share_sources_conflict"))
        self.add(
            FundamentalField.OUTSTANDING_SHARES,
            latest[1],
            UnitKind.SHARES,
            period,
            "share count series",
            "shares_full",
            "latest dated count; split basis not verified",
            invalid=conflict,
        )
        self.add(
            FundamentalField.INCREMENTAL_DILUTION,
            None,
            UnitKind.SHARES,
            period,
            "share count series",
            None,
            "no provider source",
        )
        return period


def normalize_statements(
    identity: VerifiedFundamentalIdentity,
    statements: ProviderStatements,
    expectation: ProviderExpectation,
) -> NormalizedFundamentals:
    """Normalize one retrieval; never raises for provider data conditions.

    The identity is the owner-verified registry entry the retrieval belongs to.
    Conflicts withhold every number; gaps become missing observations.
    """
    if not isinstance(identity, VerifiedFundamentalIdentity):
        raise TypeError("identity must be VerifiedFundamentalIdentity")
    if not isinstance(statements, ProviderStatements):
        raise TypeError("statements must be ProviderStatements")
    if not isinstance(expectation, ProviderExpectation):
        raise TypeError("expectation must be ProviderExpectation")
    return _Normalizer(identity, statements, expectation).run()


# --- report set ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerConfirmations:
    """Explicit owner statements the provider cannot make; default is unconfirmed."""

    ebit_definition: bool = False  # Provider EBIT matches the intended EBIT.
    share_split_basis: bool = False  # No split distorts count versus price/report.
    # EBIT and total debt treat leases alike: operating-lease cost is not deducted
    # in EBIT while the lease liability is also deducted in the bridge (or the
    # reverse), so nothing is charged twice or not at all.
    lease_treatment: bool = False

    def __post_init__(self) -> None:
        for name in ("ebit_definition", "share_split_basis", "lease_treatment"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")


def build_report_set(
    normalized: NormalizedFundamentals,
    capital_structure: CapitalStructure,
    input_version: str,
    confirmations: OwnerConfirmations = OwnerConfirmations(),
) -> FundamentalReportSet | None:
    """Assemble the M8-03 report set, or None when no usable base exists.

    None means: numbers were withheld, or there is no annual period or no dated
    share count. The capital structure (class count) always comes from the
    owner's proof, never from the provider; its date must equal the share count
    date. Manual fields stay missing observations until a ``ManualAdjustment``
    is attached by the caller.
    """
    if not isinstance(normalized, NormalizedFundamentals):
        raise TypeError("normalized must be NormalizedFundamentals")
    if not isinstance(confirmations, OwnerConfirmations):
        raise TypeError("confirmations must be OwnerConfirmations")
    if (
        normalized.numbers_withheld
        or normalized.currency is None
        or normalized.bridge_date is None
        or normalized.shares_period is None
    ):
        return None
    qualifications = list(normalized.issues)
    if not confirmations.ebit_definition:
        qualifications.append(
            QualityIssue(QualityStatus.UNCONFIRMED_ASSUMPTIONS, "ebit_definition")
        )
    if not confirmations.share_split_basis:
        qualifications.append(
            QualityIssue(QualityStatus.UNCONFIRMED_ASSUMPTIONS, "share_split_basis")
        )
    if not confirmations.lease_treatment:
        qualifications.append(
            QualityIssue(QualityStatus.UNCONFIRMED_ASSUMPTIONS, "lease_treatment")
        )
    return FundamentalReportSet(
        normalized.identity,
        normalized.report,
        input_version,
        normalized.currency,
        normalized.observations,
        capital_structure,
        normalized.bridge_date,
        normalized.shares_period,
        qualifications=tuple(dict.fromkeys(qualifications)),
    )


# --- trailing twelve months ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrailingTwelveMonths:
    value: Decimal | None
    period: FlowPeriod | None
    issues: tuple[QualityIssue, ...]


def _ttm_blocked(status: QualityStatus, code: str) -> TrailingTwelveMonths:
    return TrailingTwelveMonths(None, None, (QualityIssue(status, code),))


def trailing_twelve_months(
    entries: Sequence[tuple[FlowPeriod, Decimal | None]],
) -> TrailingTwelveMonths:
    """TTM flow from the newest four complete, contiguous, non-overlapping quarters.

    Fiscal-year, year-to-date and TTM entries are cumulative or overlapping and
    never contribute (so no period is counted twice). A missing, incomplete or
    non-contiguous quarter yields no TTM, never a partial sum. Balance-sheet
    values are instants and cannot be passed here.
    """
    for entry in entries:
        if (
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], FlowPeriod)
        ):
            raise TypeError("entries must be (FlowPeriod, Decimal | None) tuples")
        _cell(entry[1])
    quarters = sorted(
        (e for e in entries if e[0].kind is PeriodKind.QUARTER),
        key=lambda e: e[0].end,
    )
    if not quarters:
        return _ttm_blocked(QualityStatus.INCOMPLETE, "ttm_no_quarters")
    if any(b[0].start <= a[0].end for a, b in zip(quarters, quarters[1:])):
        return _ttm_blocked(QualityStatus.INVALID, "ttm_overlapping_periods")
    window = quarters[-4:]
    if len(window) < 4:
        return _ttm_blocked(QualityStatus.INCOMPLETE, "ttm_incomplete")
    for period, value in window:
        length = (period.end - period.start).days + 1
        if (
            value is None
            or not period.complete
            or not MIN_QUARTER_DAYS <= length <= MAX_QUARTER_DAYS
        ):
            return _ttm_blocked(QualityStatus.INCOMPLETE, "ttm_incomplete")
    if any(
        b[0].start != a[0].end + timedelta(days=1) for a, b in zip(window, window[1:])
    ):
        return _ttm_blocked(QualityStatus.INCOMPLETE, "ttm_incomplete")
    end = window[-1][0].end
    period = FlowPeriod(
        window[0][0].start, end, PeriodKind.TTM, f"TTM ending {end.isoformat()}", True
    )
    return TrailingTwelveMonths(sum((v for _, v in window), Decimal(0)), period, ())


__all__ = [
    "FIELD_CATALOG",
    "MANUAL_FIELDS",
    "FetchFailure",
    "NormalizedFundamentals",
    "OwnerConfirmations",
    "ProviderExpectation",
    "ProviderStatements",
    "StatementTable",
    "Suggestion",
    "TrailingTwelveMonths",
    "build_report_set",
    "normalize_statements",
    "trailing_twelve_months",
]
