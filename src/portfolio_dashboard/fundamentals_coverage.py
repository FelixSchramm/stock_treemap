"""Offline evaluation of how well provider statements cover the #70 field contract.

M8-02 (#71). This module only judges data frames and dicts that a caller hands
in; it never fetches anything and is not imported by the app. The manually run
probe in ``scripts/fundamentals_coverage_probe.py`` supplies real snapshots.

Rules that keep the verdicts honest: an absent row or a NaN cell is *missing*,
never zero; an explicit finite zero is a reported value. Floats are acceptable
here because the results are coverage verdicts, not valuation inputs.
Details name provider row labels and counts, never raw values.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

import pandas as pd

LATEST_YEARS = 3  # ADR 0010: at least three complete consecutive fiscal years.
BALANCE_DATES = LATEST_YEARS + 1  # Opening balance of the first year for delta NWC.
MAX_ANNUAL_AGE_DAYS = 550  # ADR 0010 technical staleness rule.
MAX_SHARES_AGE_DAYS = 120
FY_END_TOLERANCE_DAYS = 10  # 52/53-week calendars move the period end slightly.
SHARE_CONFLICT_RATIO = 0.10
EPS_TOLERANCE = 0.05
FCF_TOLERANCE = 0.01
UNIT_TOKENS = frozenset({"GBp", "GBX", "ZAc", "ILA"})

# Provider row labels per contract field, in order of preference.
EBIT_ROWS = ("Operating Income", "Total Operating Income As Reported")
EBIT_ALTERNATIVE_ROWS = ("EBIT",)  # Includes non-operating items: other definition.
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
LEASE_ROWS = ("Capital Lease Obligations",)
MINORITY_ROWS = ("Minority Interest",)
PREFERRED_ROWS = (
    "Preferred Stock Equity",
    "Preferred Securities Outside Stock Equity",
    "Preferred Stock",
)
BALANCE_SHARE_ROWS = ("Ordinary Shares Number",)
NET_INCOME_ROWS = ("Diluted NI Availto Com Stockholders", "Net Income Common Stockholders")
EPS_SHARE_ROWS = ("Diluted Average Shares",)
EPS_ROWS = ("Diluted EPS",)
REVENUE_ROWS = ("Total Revenue", "Operating Revenue")


class Status(Enum):
    OK = "ok"
    PARTIAL = "partial"
    MISSING = "missing"
    PROBLEM = "problem"


# The ten contract fields counted as "x of N instruments" in the public report.
REQUIRED_FIELDS = (
    "ebit",
    "tax",
    "da",
    "capex",
    "nwc",
    "cash",
    "financial_debt",
    "minority",
    "preferred",
    "shares",
)
# Further quality checks; they qualify the data rather than being fields.
QUALITY_CHECKS = (
    "identity",
    "reporting_currency",
    "annual_history",
    "quarterly_history",
    "fiscal_year_end",
    "freshness",
    "capex_sign",
    "scale_consistency",
    "publication_date",
)
ALL_CHECKS = REQUIRED_FIELDS + QUALITY_CHECKS


@dataclass(frozen=True, slots=True)
class CoverageTarget:
    """Owner-confirmed expectations for one instrument; no provider claims."""

    case_id: str
    trading_currency: str
    expected_reporting_currency: str
    fiscal_year_end: str  # "MM-DD"
    market_class: str  # Coarse class, e.g. "Xetra EUR"; safe for public aggregates.
    symbol: str = field(repr=False, default="")
    label: str = field(repr=False, default="")


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    """Provider data for one instrument. None means the dataset was unavailable."""

    annual_income: pd.DataFrame | None = None
    annual_balance: pd.DataFrame | None = None
    annual_cashflow: pd.DataFrame | None = None
    quarterly_income: pd.DataFrame | None = None
    quarterly_balance: pd.DataFrame | None = None
    quarterly_cashflow: pd.DataFrame | None = None
    info: Mapping | None = None
    shares_full: pd.Series | None = None
    earnings_dates: Sequence[date] | None = None
    failed_datasets: tuple[str, ...] = ()  # Exception class names only.


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    status: Status
    detail: str
    codes: tuple[str, ...] = ()  # Stable problem/information classes.


@dataclass(frozen=True, slots=True)
class InstrumentCoverage:
    target: CoverageTarget
    results: tuple[CheckResult, ...]

    def result(self, check: str) -> CheckResult:
        return next(r for r in self.results if r.check == check)


# --- primitives -----------------------------------------------------------


def finite(value: object) -> float | None:
    """Return a finite float, or None for NaN/inf/non-numeric. Zero stays zero."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_date(value: object) -> date | None:
    try:
        stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(stamp) else stamp.date()


def period_ends(frame: pd.DataFrame | None) -> list[date]:
    """Period-end dates of columns holding at least one finite value, newest first."""
    if frame is None or frame.empty:
        return []
    ends = []
    for column in frame.columns:
        end = _as_date(column)
        if end is not None and any(finite(v) is not None for v in frame[column]):
            ends.append(end)
    return sorted(set(ends), reverse=True)


def cell(frame: pd.DataFrame | None, rows: Sequence[str], end: date) -> float | None:
    """First finite value among the preferred row labels at one period end."""
    if frame is None or frame.empty:
        return None
    for row in rows:
        if row not in frame.index:
            continue
        for column in frame.columns:
            if _as_date(column) == end:
                value = finite(frame.loc[row, column])  # Duplicated column -> None.
                if value is not None:
                    return value
    return None


def _row_used(frame: pd.DataFrame | None, rows: Sequence[str], ends: Sequence[date]):
    for row in rows:
        if any(cell(frame, (row,), end) is not None for end in ends):
            return row
    return None


def consecutive_chain(ends: Sequence[date], low: int, high: int) -> int:
    """Length of the newest chain whose neighbours are `low`..`high` days apart."""
    ordered = sorted(ends, reverse=True)
    if not ordered:
        return 0
    chain = 1
    for newer, older in zip(ordered, ordered[1:], strict=False):
        if low <= (newer - older).days <= high:
            chain += 1
        else:
            break
    return chain


def annual_chain(ends: Sequence[date]) -> int:
    return consecutive_chain(ends, 350, 380)


def quarterly_chain(ends: Sequence[date]) -> int:
    return consecutive_chain(ends, 80, 105)


def _coverage(
    frame: pd.DataFrame | None, rows: Sequence[str], ends: Sequence[date]
) -> int:
    return sum(cell(frame, rows, end) is not None for end in ends)


def _grade(present: int, wanted: int) -> Status:
    if wanted and present >= wanted:
        return Status.OK
    return Status.PARTIAL if present else Status.MISSING


# --- required fields --------------------------------------------------------


def _result_for_ends(
    check: str,
    frame: pd.DataFrame | None,
    rows: Sequence[str],
    ends: Sequence[date],
    dataset: str,
    failed: Sequence[str],
    codes: tuple[str, ...] = (),
) -> CheckResult:
    if dataset in failed:
        return CheckResult(check, Status.MISSING, f"{dataset} unavailable", ("fetch_failed",))
    present = _coverage(frame, rows, ends)
    status = _grade(present, len(ends))
    label = _row_used(frame, rows, ends)
    if status is Status.OK:
        return CheckResult(check, status, f"{present}/{len(ends)} years via '{label}'", codes)
    if label is None:
        absent = [r for r in rows if frame is None or r not in frame.index]
        reason = "row_absent" if len(absent) == len(rows) else "nan_values"
        return CheckResult(check, status, f"0/{len(ends)} years; {reason}", (reason,))
    return CheckResult(
        check, status, f"{present}/{len(ends)} years via '{label}'", ("gaps_in_years",)
    )


def _ebit(snapshot: ProviderSnapshot, ends: list[date]) -> CheckResult:
    result = _result_for_ends(
        "ebit",
        snapshot.annual_income,
        EBIT_ROWS,
        ends,
        "annual_income",
        snapshot.failed_datasets,
    )
    if result.status is Status.OK:
        values = [cell(snapshot.annual_income, EBIT_ROWS, e) for e in ends]
        if any(v is not None and v <= 0 for v in values):
            # Coverage is fine; the MVP model still needs a positive EBIT base.
            result = CheckResult(
                "ebit", Status.OK, result.detail, (*result.codes, "ebit_not_positive")
            )
    if result.status is Status.MISSING and "fetch_failed" not in result.codes:
        alternative = _coverage(snapshot.annual_income, EBIT_ALTERNATIVE_ROWS, ends)
        if alternative:
            return CheckResult(
                "ebit",
                Status.PARTIAL,
                f"only provider 'EBIT' ({alternative}/{len(ends)} years); "
                "definition includes non-operating items",
                ("definition_differs",),
            )
    return result


def _tax(snapshot: ProviderSnapshot, ends: list[date]) -> CheckResult:
    frame = snapshot.annual_income
    if "annual_income" in snapshot.failed_datasets:
        return CheckResult("tax", Status.MISSING, "annual_income unavailable", ("fetch_failed",))
    both = sum(
        cell(frame, TAX_ROWS, e) is not None and cell(frame, PRETAX_ROWS, e) is not None
        for e in ends
    )
    status = _grade(both, len(ends))
    codes = ("tax_is_effective_not_operating",) if status is not Status.MISSING else ()
    return CheckResult(
        "tax", status, f"{both}/{len(ends)} years with tax expense and pretax income", codes
    )


def _nwc(snapshot: ProviderSnapshot, balance_ends: list[date]) -> CheckResult:
    frame = snapshot.annual_balance
    if "annual_balance" in snapshot.failed_datasets:
        return CheckResult("nwc", Status.MISSING, "annual_balance unavailable", ("fetch_failed",))
    wanted = balance_ends[:BALANCE_DATES]
    parts = (CURRENT_ASSET_ROWS, CURRENT_LIABILITY_ROWS, CASH_ROWS, CURRENT_DEBT_ROWS)
    complete = sum(all(cell(frame, rows, e) is not None for rows in parts) for e in wanted)
    status = _grade(complete, BALANCE_DATES)
    codes: list[str] = []
    missing_parts = [
        rows[0]
        for rows in parts
        if any(cell(frame, rows, e) is None for e in wanted)
    ]
    if missing_parts:
        codes.append("component_missing")
    return CheckResult(
        "nwc",
        status,
        f"{complete}/{BALANCE_DATES} balance dates with all four components"
        + (f"; missing: {', '.join(missing_parts)}" if missing_parts else ""),
        tuple(codes),
    )


def _bridge(
    check: str,
    snapshot: ProviderSnapshot,
    rows: Sequence[str],
    balance_ends: list[date],
    extra_codes: tuple[str, ...] = (),
) -> CheckResult:
    if "annual_balance" in snapshot.failed_datasets:
        return CheckResult(check, Status.MISSING, "annual_balance unavailable", ("fetch_failed",))
    if not balance_ends:
        return CheckResult(check, Status.MISSING, "no balance date", ("no_balance_date",))
    latest = balance_ends[0]
    value = cell(snapshot.annual_balance, rows, latest)
    if value is not None:
        return CheckResult(check, Status.OK, "present at latest annual balance date", extra_codes)
    frame = snapshot.annual_balance
    row_known = frame is not None and any(r in frame.index for r in rows)
    code = "nan_at_latest" if row_known else "row_absent_not_zero"
    return CheckResult(check, Status.MISSING, f"not reported by provider ({code})", (code,))


def _shares(snapshot: ProviderSnapshot, balance_ends: list[date], as_of: date):
    info = snapshot.info or {}
    sources: dict[str, float] = {}
    for key in ("sharesOutstanding", "impliedSharesOutstanding"):
        value = finite(info.get(key))
        if value is not None and value > 0:
            sources[key] = value
    dated_age: int | None = None
    series = snapshot.shares_full
    if series is not None and len(series):
        clean = series.dropna()
        clean = clean[clean > 0]
        if len(clean):
            sources["shares_full"] = float(clean.iloc[-1])
            last = _as_date(clean.index[-1])
            dated_age = (as_of - last).days if last else None
    if balance_ends:
        value = cell(snapshot.annual_balance, BALANCE_SHARE_ROWS, balance_ends[0])
        if value is not None and value > 0:
            sources["balance_sheet"] = value
    if not sources:
        return CheckResult("shares", Status.MISSING, "no positive share count", ("no_share_count",))
    codes: list[str] = []
    values = list(sources.values())
    if len(values) > 1 and max(values) / min(values) - 1 > SHARE_CONFLICT_RATIO:
        return CheckResult(
            "shares",
            Status.PROBLEM,
            f"sources {sorted(sources)} differ by more than {SHARE_CONFLICT_RATIO:.0%}",
            ("share_sources_conflict",),
        )
    current = {"sharesOutstanding", "impliedSharesOutstanding", "shares_full"} & set(sources)
    if not current:
        return CheckResult(
            "shares",
            Status.PARTIAL,
            "only balance-sheet count (no current count)",
            ("only_balance_date_shares",),
        )
    if dated_age is None:
        codes.append("shares_date_unknown")
        return CheckResult(
            "shares", Status.PARTIAL, f"count from {sorted(current)}; no date", tuple(codes)
        )
    if dated_age > MAX_SHARES_AGE_DAYS:
        return CheckResult(
            "shares",
            Status.PARTIAL,
            f"dated count is {dated_age} days old (limit {MAX_SHARES_AGE_DAYS})",
            ("shares_date_stale",),
        )
    return CheckResult("shares", Status.OK, f"dated count, {dated_age} days old", ())


# --- quality checks ---------------------------------------------------------


def _identity(target: CoverageTarget, snapshot: ProviderSnapshot) -> CheckResult:
    info = snapshot.info
    if not info:
        return CheckResult("identity", Status.MISSING, "no metadata", ("no_metadata",))
    codes: list[str] = []
    if info.get("quoteType") != "EQUITY":
        codes.append("quote_type_not_equity")
    currency = info.get("currency")
    if currency != target.trading_currency:
        codes.append("trading_currency_mismatch")
    if currency in UNIT_TOKENS:
        codes.append("sub_unit_quote_token")
    if codes:
        return CheckResult("identity", Status.PROBLEM, "; ".join(codes), tuple(codes))
    return CheckResult("identity", Status.OK, "EQUITY, trading currency as confirmed", ())


def _reporting_currency(target: CoverageTarget, snapshot: ProviderSnapshot) -> CheckResult:
    reported = (snapshot.info or {}).get("financialCurrency")
    if not isinstance(reported, str) or not reported:
        return CheckResult("reporting_currency", Status.MISSING, "financialCurrency absent", ("absent",))
    if reported != target.expected_reporting_currency:
        return CheckResult(
            "reporting_currency",
            Status.PROBLEM,
            "financialCurrency differs from owner-confirmed currency",
            ("reporting_currency_differs_from_expected",),
        )
    codes = ("cross_currency",) if reported != target.trading_currency else ()
    return CheckResult("reporting_currency", Status.OK, "as expected", codes)


def _annual_history(snapshot: ProviderSnapshot, ends: dict[str, list[date]]) -> CheckResult:
    if {"annual_income", "annual_balance", "annual_cashflow"} & set(snapshot.failed_datasets):
        return CheckResult("annual_history", Status.MISSING, "statement fetch failed", ("fetch_failed",))
    chains = {name: annual_chain(values) for name, values in ends.items()}
    counts = {name: len(values) for name, values in ends.items()}
    detail = ", ".join(
        f"{name}: {counts[name]} periods/{chains[name]} consecutive" for name in sorted(ends)
    )
    enough = (
        chains["income"] >= LATEST_YEARS
        and chains["cashflow"] >= LATEST_YEARS
        and chains["balance"] >= BALANCE_DATES
    )
    codes = tuple(
        f"gap_{name}" for name in sorted(ends) if counts[name] > chains[name]
    )
    if enough:
        return CheckResult("annual_history", Status.OK, detail, codes)
    if any(counts.values()):
        return CheckResult("annual_history", Status.PARTIAL, detail, codes + ("too_short",))
    return CheckResult("annual_history", Status.MISSING, detail, ("no_history",))


def _quarterly_history(snapshot: ProviderSnapshot) -> CheckResult:
    sets = {
        "income": period_ends(snapshot.quarterly_income),
        "balance": period_ends(snapshot.quarterly_balance),
        "cashflow": period_ends(snapshot.quarterly_cashflow),
    }
    chains = {name: quarterly_chain(values) for name, values in sets.items()}
    detail = ", ".join(
        f"{name}: {len(sets[name])} periods/{chains[name]} consecutive" for name in sorted(sets)
    )
    codes = tuple(f"gap_{name}" for name in sorted(sets) if len(sets[name]) > chains[name])
    if min(chains.values()) >= 4:
        return CheckResult("quarterly_history", Status.OK, detail, codes)
    if any(sets.values()):
        return CheckResult("quarterly_history", Status.PARTIAL, detail, codes + ("under_four_quarters",))
    return CheckResult("quarterly_history", Status.MISSING, detail, ("no_history",))


def _fiscal_year_end(target: CoverageTarget, ends: list[date]) -> CheckResult:
    if not ends:
        return CheckResult("fiscal_year_end", Status.MISSING, "no annual period", ("no_history",))
    month, day = (int(part) for part in target.fiscal_year_end.split("-"))
    off = 0
    for end in ends[:LATEST_YEARS]:
        deltas = []
        for year in (end.year - 1, end.year, end.year + 1):
            try:
                deltas.append(abs((end - date(year, month, day)).days))
            except ValueError:  # 29 Feb in a non-leap year.
                continue
        off += min(deltas, default=9999) > FY_END_TOLERANCE_DAYS
    if off:
        return CheckResult(
            "fiscal_year_end",
            Status.PROBLEM,
            f"{off} of {min(len(ends), LATEST_YEARS)} period ends deviate from the confirmed year end",
            ("fiscal_year_end_mismatch",),
        )
    return CheckResult("fiscal_year_end", Status.OK, "period ends match confirmed year end", ())


def _freshness(snapshot: ProviderSnapshot, ends: list[date], as_of: date) -> CheckResult:
    if not ends:
        return CheckResult("freshness", Status.MISSING, "no annual period", ("no_history",))
    age = (as_of - ends[0]).days
    codes: list[str] = []
    newer = [
        d
        for d in period_ends(snapshot.quarterly_balance)
        if d > ends[0] and (d - ends[0]).days > 0
    ]
    if newer:
        codes.append("newer_interim_available")
    if age < 0:
        return CheckResult("freshness", Status.PROBLEM, "period end after reference date", ("future_period",))
    if age > MAX_ANNUAL_AGE_DAYS:
        return CheckResult(
            "freshness",
            Status.PROBLEM,
            f"latest annual period is {age} days old (limit {MAX_ANNUAL_AGE_DAYS})",
            ("stale_annual_report", *codes),
        )
    return CheckResult("freshness", Status.OK, f"latest annual period {age} days old", tuple(codes))


def _capex_sign(snapshot: ProviderSnapshot, ends: list[date]) -> CheckResult:
    values = [cell(snapshot.annual_cashflow, CAPEX_ROWS, e) for e in ends]
    values = [v for v in values if v is not None]
    if not values:
        # Provider FCF that equals operating cash flow means capex was silently dropped.
        dropped = any(
            (operating := cell(snapshot.annual_cashflow, OPERATING_CASH_ROWS, e)) is not None
            and operating == cell(snapshot.annual_cashflow, FREE_CASH_ROWS, e)
            for e in ends
        )
        codes = ("no_capex", "fcf_equals_operating_cash_flow") if dropped else ("no_capex",)
        return CheckResult("capex_sign", Status.MISSING, "no capex value", codes)
    nonzero = [v for v in values if v != 0]
    if not nonzero:
        return CheckResult("capex_sign", Status.PARTIAL, "only reported zero", ("capex_zero",))
    if all(v < 0 for v in nonzero):
        sign_code = "capex_outflow_negative"
    elif all(v > 0 for v in nonzero):
        sign_code = "capex_outflow_positive"
    else:
        return CheckResult("capex_sign", Status.PROBLEM, "mixed signs across years", ("capex_sign_mixed",))
    # Control: FCF should equal operating cash flow plus (negative) capex.
    codes = [sign_code]
    for end in ends:
        capex = cell(snapshot.annual_cashflow, CAPEX_ROWS[:1], end)
        operating = cell(snapshot.annual_cashflow, OPERATING_CASH_ROWS, end)
        free = cell(snapshot.annual_cashflow, FREE_CASH_ROWS, end)
        if None in (capex, operating, free) or operating == 0:
            continue
        if abs(operating + capex - free) > FCF_TOLERANCE * abs(operating):
            return CheckResult(
                "capex_sign",
                Status.PROBLEM,
                "provider FCF does not equal operating cash flow plus capex",
                (sign_code, "fcf_identity_failed"),
            )
        codes.append("fcf_identity_holds")
        break
    return CheckResult("capex_sign", Status.OK, "single sign convention across years", tuple(codes))


def _scale_consistency(snapshot: ProviderSnapshot, ends: list[date]) -> CheckResult:
    """Diluted EPS ~ net income / average diluted shares verifies money/share scale."""
    for end in ends:
        income = cell(snapshot.annual_income, NET_INCOME_ROWS, end)
        shares = cell(snapshot.annual_income, EPS_SHARE_ROWS, end)
        eps = cell(snapshot.annual_income, EPS_ROWS, end)
        if income is None or shares is None or eps is None or shares <= 0 or eps == 0:
            continue
        ratio = (income / shares) / eps
        if abs(ratio - 1) <= EPS_TOLERANCE:
            return CheckResult("scale_consistency", Status.OK, "EPS identity holds", ())
        return CheckResult(
            "scale_consistency",
            Status.PROBLEM,
            "EPS identity violated (scale, currency or share basis)",
            ("eps_identity_failed",),
        )
    return CheckResult("scale_consistency", Status.MISSING, "EPS inputs unavailable", ("no_eps_inputs",))


def _publication(snapshot: ProviderSnapshot, ends: list[date]) -> CheckResult:
    """Statements carry no publication date; earnings events are only a proxy."""
    dates = snapshot.earnings_dates
    if not dates:
        return CheckResult("publication_date", Status.MISSING, "no date source", ("no_publication_date",))
    matched = sum(
        any(end < d <= end + timedelta(days=200) for d in dates) for end in ends[:LATEST_YEARS]
    )
    return CheckResult(
        "publication_date",
        Status.PARTIAL if matched else Status.MISSING,
        f"event dates near {matched}/{min(len(ends), LATEST_YEARS)} year ends; "
        "no statement-level publication field",
        ("proxy_event_dates_only",) if matched else ("no_publication_date",),
    )


# --- public entry points ---------------------------------------------------


def evaluate_instrument(
    target: CoverageTarget, snapshot: ProviderSnapshot, as_of: date
) -> InstrumentCoverage:
    """Judge one snapshot against the field contract at an explicit reference date."""
    income_ends = period_ends(snapshot.annual_income)
    reference = income_ends[:LATEST_YEARS]
    cash_ends = period_ends(snapshot.annual_cashflow)
    balance_ends = period_ends(snapshot.annual_balance)
    failed = snapshot.failed_datasets
    results = (
        _ebit(snapshot, reference),
        _tax(snapshot, reference),
        _result_for_ends(
            "da", snapshot.annual_cashflow, DA_ROWS, reference, "annual_cashflow", failed
        ),
        _result_for_ends(
            "capex", snapshot.annual_cashflow, CAPEX_ROWS, reference, "annual_cashflow", failed
        ),
        _nwc(snapshot, balance_ends),
        _bridge("cash", snapshot, CASH_ROWS, balance_ends),
        _bridge(
            "financial_debt",
            snapshot,
            DEBT_ROWS,
            balance_ends,
            ("lease_row_visible",)
            if cell(snapshot.annual_balance, LEASE_ROWS, balance_ends[0] if balance_ends else as_of)
            is not None
            else ("lease_split_not_visible",),
        ),
        _bridge("minority", snapshot, MINORITY_ROWS, balance_ends),
        _bridge("preferred", snapshot, PREFERRED_ROWS, balance_ends),
        _shares(snapshot, balance_ends, as_of),
        _identity(target, snapshot),
        _reporting_currency(target, snapshot),
        _annual_history(
            snapshot, {"income": income_ends, "balance": balance_ends, "cashflow": cash_ends}
        ),
        _quarterly_history(snapshot),
        _fiscal_year_end(target, income_ends),
        _freshness(snapshot, income_ends, as_of),
        _capex_sign(snapshot, reference),
        _scale_consistency(snapshot, reference),
        _publication(snapshot, income_ends),
    )
    return InstrumentCoverage(target, results)


def aggregate(coverages: Sequence[InstrumentCoverage]) -> dict:
    """Counts only: per check status counts, problem-code counts and market classes."""
    per_check = {
        check: Counter(c.result(check).status.value for c in coverages) for check in ALL_CHECKS
    }
    codes = Counter(
        (r.check, code) for c in coverages for r in c.results for code in r.codes
    )
    classes = Counter(c.target.market_class for c in coverages)
    return {
        "cases": len(coverages),
        "per_check": per_check,
        "codes": codes,
        "market_classes": classes,
    }


def render_aggregate_markdown(summary: dict) -> str:
    """Aggregate table safe for public documentation (no instrument identifiers)."""
    total = summary["cases"]
    lines = [
        f"| Prüfung | OK | teilweise | fehlt | Problem | (von {total}) |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for check in ALL_CHECKS:
        counts = summary["per_check"][check]
        lines.append(
            f"| {check} | {counts['ok']} | {counts['partial']} | {counts['missing']} "
            f"| {counts['problem']} | |"
        )
    lines += ["", "| Prüfung | Klasse | Fälle |", "| --- | --- | ---: |"]
    for (check, code), count in sorted(summary["codes"].items()):
        lines.append(f"| {check} | {code} | {count} |")
    return "\n".join(lines)


def render_instrument_markdown(coverage: InstrumentCoverage) -> str:
    """Per-instrument detail. For private reports only; includes the owner's label."""
    lines = [f"### {coverage.target.case_id} {coverage.target.label}".rstrip()]
    lines.append(f"Klasse: {coverage.target.market_class}\n")
    lines += ["| Prüfung | Status | Detail | Klassen |", "| --- | --- | --- | --- |"]
    for r in coverage.results:
        lines.append(f"| {r.check} | {r.status.value} | {r.detail} | {', '.join(r.codes)} |")
    return "\n".join(lines)


def key_values(snapshot: ProviderSnapshot) -> dict[str, object]:
    """Latest-fiscal-year revenue, EBIT, D&A, capex for private plausibility checks."""
    ends = period_ends(snapshot.annual_income)
    if not ends:
        return {}
    end = ends[0]
    return {
        "period_end": end.isoformat(),
        "revenue": cell(snapshot.annual_income, REVENUE_ROWS, end),
        "ebit": cell(snapshot.annual_income, EBIT_ROWS, end),
        "da": cell(snapshot.annual_cashflow, DA_ROWS, end),
        "capex": cell(snapshot.annual_cashflow, CAPEX_ROWS, end),
    }


VENUE_GROUPS = {
    "NASDAQ": "US-Listing",
    "NYSE": "US-Listing",
    "XETRA": "Xetra",
    "HKEX": "Hongkong",
}


def market_class(entry: Mapping) -> str:
    """Coarse, non-identifying class of a universe entry for public aggregates."""
    venue = str(entry.get("venue", "")).upper()
    label = f"{VENUE_GROUPS.get(venue, 'sonstige Börse')} {entry['trading_currency']}"
    if entry["expected_reporting_currency"] != entry["trading_currency"]:
        label += f" (Berichtswährung {entry['expected_reporting_currency']})"
    return label


def load_universe(data: Mapping) -> tuple[CoverageTarget, ...]:
    """Build targets from the owner's private universe mapping; requires live approval."""
    if data.get("live_test_approved") is not True:
        raise ValueError("live test not approved in the universe file")
    entries = data.get("instruments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("universe has no instruments")
    targets = []
    for index, entry in enumerate(entries, 1):
        for key in (
            "yahoo_symbol",
            "trading_currency",
            "expected_reporting_currency",
            "fiscal_year_end",
        ):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"entry {index}: {key} must be non-empty text")
        if entry.get("quote_unit_scale") != 1:
            raise ValueError(f"entry {index}: only quote_unit_scale 1 is covered")
        targets.append(
            CoverageTarget(
                case_id=f"F-{index:02d}",
                trading_currency=entry["trading_currency"],
                expected_reporting_currency=entry["expected_reporting_currency"],
                fiscal_year_end=entry["fiscal_year_end"],
                market_class=market_class(entry),
                symbol=entry["yahoo_symbol"],
                label=str(entry.get("name", "")),
            )
        )
    return tuple(targets)
