"""Entirely invented identities, evidence and numbers; no provider responses."""

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from portfolio_dashboard.fundamentals import (
    BRIDGE_FIELDS,
    CapitalStructure,
    CurrentSharesPeriod,
    EvidenceKind,
    FieldProvenance,
    FlowPeriod,
    FundamentalField,
    FundamentalObservation,
    FundamentalRegistry,
    FundamentalReportSet,
    FundamentalSource,
    FundamentalUnit,
    InstantPeriod,
    IssuerKind,
    ManualAdjustment,
    PeriodKind,
    QualityIssue,
    QualityStatus,
    ReportIdentity,
    UnitKind,
    VerifiedFundamentalIdentity,
    WeightedSharesPeriod,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    QuoteUnit,
    ValueStatus,
)

D = Decimal
AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SOURCE = FundamentalSource("synthetic-provider", "synthetic-company")
REPORT = ReportIdentity(
    "synthetic-issuer", "synthetic-report-set", "v1", "synthetic-group"
)
MONEY = FundamentalUnit(UnitKind.MONEY, D("1000000"), "EUR")
RATE = FundamentalUnit(UnitKind.RATE, D(1))
SHARES = FundamentalUnit(UnitKind.SHARES, D("1000000"))
CURRENT = CurrentSharesPeriod(date(2025, 12, 31), "synthetic-split-basis")


def identity():
    return VerifiedFundamentalIdentity(
        VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synthetic-share",
                display_ticker="SYNTHETIC",
                trading_venue="synthetic-venue",
            ),
            equity_form=EquityForm.SHARE,
        ),
        "synthetic-class",
        "synthetic-issuer",
        SOURCE,
        "synthetic-group",
        IssuerKind.NONFINANCIAL,
        "synthetic-identity-proof",
        AT,
    )


def period(year):
    return FlowPeriod(
        date(year, 1, 1), date(year, 12, 31), PeriodKind.FISCAL_YEAR, str(year), True
    )


def observation(field=FundamentalField.EBIT, value=D(100), when=None, unit=None):
    if when is None:
        when = period(2024)
    if unit is None:
        unit = (
            RATE
            if field is FundamentalField.OPERATING_TAX_RATE
            else SHARES
            if field
            in {
                FundamentalField.OUTSTANDING_SHARES,
                FundamentalField.INCREMENTAL_DILUTION,
                FundamentalField.WEIGHTED_EPS_SHARES,
            }
            else MONEY
        )
    return FundamentalObservation(
        field,
        value,
        ValueStatus.AVAILABLE,
        unit,
        when,
        REPORT,
        FieldProvenance(
            SOURCE,
            EvidenceKind.PROVIDER,
            "synthetic-field",
            "synthetic definition",
            "explicit identity normalization",
            AT,
        ),
    )


def report_set():
    observations = []
    for year in (2022, 2023, 2024):
        for field, value in (
            (FundamentalField.EBIT, D(100)),
            (FundamentalField.OPERATING_TAX_RATE, D("0.2")),
            (FundamentalField.DEPRECIATION_AMORTIZATION, D(20)),
            (FundamentalField.CAPEX, D(40)),
        ):
            observations.append(observation(field, value, period(year)))
    for year in (2021, 2022, 2023, 2024):
        observations.append(
            observation(
                FundamentalField.OPERATING_NWC, D(15), InstantPeriod(date(year, 12, 31))
            )
        )
    for field in sorted(BRIDGE_FIELDS, key=lambda f: f.value):
        observations.append(observation(field, D(0), InstantPeriod(CURRENT.as_of)))
    observations.extend(
        (
            observation(FundamentalField.OUTSTANDING_SHARES, D(10), CURRENT),
            observation(FundamentalField.INCREMENTAL_DILUTION, D(0), CURRENT),
        )
    )
    return FundamentalReportSet(
        identity(),
        REPORT,
        "synthetic-input-v1",
        "EUR",
        tuple(observations),
        CapitalStructure(
            "synthetic-issuer",
            SOURCE,
            CURRENT.as_of,
            1,
            True,
            "synthetic-complete-capital-proof",
            AT,
        ),
        CURRENT.as_of,
        CURRENT,
    )


def blocking(report, at=AT):
    return {issue.code for issue in report.issues_at(at) if issue.blocking}


def test_complete_structural_input_preserves_zero_and_unknown_publication():
    report = report_set()
    assert not blocking(report)
    assert all(o.provenance.published_at is None for o in report.observations)
    assert all(o.provenance.reported_at is None for o in report.observations)
    assert all(o.provenance.retrieved_at == AT for o in report.observations)
    assert QualityStatus.UNKNOWN_PUBLICATION in {i.status for i in report.issues_at(AT)}
    assert report.observations[-1].value == 0
    with pytest.raises(FrozenInstanceError):
        report.input_version = "cannot-mutate"


def test_registry_shares_exact_security_across_listings_and_callers():
    report = report_set()
    first = report.identity
    second = replace(
        first,
        listing=replace(
            first.listing,
            instrument=replace(
                first.listing.instrument,
                trading_venue="synthetic-other-venue",
                quote_unit=QuoteUnit("GBP", D("0.01")),
            ),
        ),
    )
    registry = FundamentalRegistry((first, second), (report,))
    portfolio_instrument = first.listing.instrument
    watchlist_instrument = second.listing.instrument
    assert registry.resolve(portfolio_instrument) is report
    assert registry.resolve(watchlist_instrument) is report
    assert not any(
        name in report.__dataclass_fields__
        for name in ("quantity", "cost_basis", "position", "owner", "category")
    )
    for candidate in (
        Instrument(InstrumentType.STOCK, display_ticker="SYNTHETIC"),
        replace(portfolio_instrument, instrument_id="synthetic-other-share"),
        replace(portfolio_instrument, trading_venue="synthetic-unverified-venue"),
        replace(watchlist_instrument, quote_unit=QuoteUnit("EUR", D(1))),
    ):
        assert registry.resolve(candidate) is None


def test_another_class_and_adr_do_not_inherit_selected_data():
    report = report_set()
    ordinary = report.identity
    other = replace(
        ordinary,
        share_class_id="synthetic-other-class",
        listing=replace(
            ordinary.listing,
            instrument=replace(
                ordinary.listing.instrument, instrument_id="synthetic-other-share"
            ),
        ),
    )
    adr = replace(
        other,
        share_class_id="synthetic-adr-class",
        listing=replace(
            other.listing,
            equity_form=EquityForm.ADR,
            instrument=replace(other.listing.instrument, instrument_id="synthetic-adr"),
        ),
    )
    registry = FundamentalRegistry((ordinary, other, adr), (report,))
    assert registry.resolve(other.listing.instrument) is None
    assert registry.resolve(adr.listing.instrument) is None
    assert adr.issues[0].status is QualityStatus.UNSUPPORTED_MODEL


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer_id": "synthetic-wrong-issuer"},
        {"share_class_id": "synthetic-wrong-class"},
        {"source": FundamentalSource("synthetic-provider", "synthetic-other-entity")},
    ],
)
def test_registry_rejects_conflicting_verified_security(changes):
    first = identity()
    other = replace(
        first,
        **changes,
        listing=replace(
            first.listing,
            instrument=replace(
                first.listing.instrument, trading_venue="synthetic-other-venue"
            ),
        ),
    )
    with pytest.raises(ValueError, match="conflicting"):
        FundamentalRegistry((first, other), ())


def test_registry_rejects_source_collision_and_unverified_data():
    first = identity()
    other = replace(
        first,
        issuer_id="synthetic-other-issuer",
        share_class_id="synthetic-other-class",
        listing=replace(
            first.listing,
            instrument=replace(
                first.listing.instrument, instrument_id="synthetic-other-share"
            ),
        ),
    )
    with pytest.raises(ValueError, match="source assigned"):
        FundamentalRegistry((first, other), ())
    with pytest.raises(ValueError, match="verified registry"):
        FundamentalRegistry((), (report_set(),))
    with pytest.raises(ValueError, match="one selected"):
        report = report_set()
        FundamentalRegistry((report.identity,), (report, report))


@pytest.mark.parametrize(
    "count,expected",
    [
        (None, QualityStatus.INCOMPLETE),
        (2, QualityStatus.UNSUPPORTED_MODEL),
        (3, QualityStatus.UNSUPPORTED_MODEL),
        (1, None),
    ],
)
def test_complete_issuer_class_count_gates_even_when_mapping_is_verified(
    count, expected
):
    report = report_set()
    capital = replace(report.capital_structure, ordinary_class_count=count)
    issues = replace(report, capital_structure=capital).issues_at(AT)
    relevant = [
        i.status
        for i in issues
        if i.code in {"ordinary_class_count_unknown", "multiple_classes"}
    ]
    assert relevant == ([] if expected is None else [expected])


@pytest.mark.parametrize(
    "changes",
    [
        {"includes_unlisted": False},
        {"evidence_reference": None},
        {"verified_at": None},
        {"ordinary_class_count": 0},
        {"ordinary_class_count": True},
    ],
)
def test_class_count_cannot_be_inferred_from_listings_or_incomplete_evidence(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(report_set().capital_structure, **changes)


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity"), 0.0])
def test_nonfinite_and_float_observations_are_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        observation(value=value)


@pytest.mark.parametrize("status", [ValueStatus.MISSING, ValueStatus.INVALID])
def test_missing_and_invalid_values_have_no_usable_number(status):
    item = replace(observation(), value=None, status=status)
    assert item.value is None
    assert item.issues[0].blocking
    with pytest.raises(ValueError, match="no usable number"):
        replace(item, value=D(0))


@pytest.mark.parametrize(
    "unit",
    [
        None,
        FundamentalUnit(UnitKind.MONEY, None, "EUR"),
        FundamentalUnit(UnitKind.MONEY, D(1), None),
    ],
)
def test_unknown_currency_or_scale_blocks_without_default(unit):
    report = report_set()
    item = replace(report.observations[0], unit=unit)
    assert "unknown_unit" in blocking(
        replace(report, observations=(item,) + report.observations[1:])
    )


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"unit": SHARES}, "unit kind"),
        ({"period": InstantPeriod(date(2024, 12, 31))}, "FlowPeriod"),
    ],
)
def test_flow_unit_and_period_kinds_are_distinct(changes, error):
    with pytest.raises((ValueError, TypeError), match=error):
        replace(observation(), **changes)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"report": replace(REPORT, revision="v2")}, "mixed reports"),
        (
            {"report": replace(REPORT, consolidation_scope="synthetic-other-scope")},
            "mixed reports",
        ),
        ({"unit": FundamentalUnit(UnitKind.MONEY, D(1), "USD")}, "currencies"),
    ],
)
def test_report_set_rejects_mixed_restated_accounts_scope_and_currency(changes, error):
    report = report_set()
    item = replace(report.observations[0], **changes)
    with pytest.raises(ValueError, match=error):
        replace(report, observations=(item,) + report.observations[1:])


def test_report_set_rejects_unverified_field_source_and_duplicate_period():
    report = report_set()
    item = replace(
        report.observations[0],
        provenance=replace(
            report.observations[0].provenance,
            source=FundamentalSource(
                "synthetic-other-provider", "synthetic-other-entity"
            ),
        ),
    )
    with pytest.raises(ValueError, match="unverified fundamental source"):
        replace(report, observations=(item,) + report.observations[1:])
    with pytest.raises(ValueError, match="duplicate"):
        replace(report, observations=report.observations + (report.observations[0],))


def test_mixed_periods_missing_nwc_and_current_vs_weighted_shares_block():
    report = report_set()
    quarter = FlowPeriod(
        date(2024, 1, 1), date(2024, 3, 31), PeriodKind.QUARTER, "synthetic-Q1", True
    )
    extra = observation(when=quarter)
    assert "mixed_flow_periods" in blocking(
        replace(report, observations=report.observations + (extra,))
    )
    without_opening = tuple(
        o for o in report.observations if o.period != InstantPeriod(date(2021, 12, 31))
    )
    assert "historical_components" in blocking(
        replace(report, observations=without_opening)
    )
    without_current = tuple(
        o
        for o in report.observations
        if o.field is not FundamentalField.OUTSTANDING_SHARES
    )
    weighted = observation(
        FundamentalField.WEIGHTED_EPS_SHARES,
        D(10),
        WeightedSharesPeriod(period(2024), CURRENT.split_basis),
    )
    assert "current_shares" in blocking(
        replace(report, observations=without_current + (weighted,))
    )
    with pytest.raises(TypeError, match="CurrentSharesPeriod"):
        replace(weighted, field=FundamentalField.OUTSTANDING_SHARES)


def test_invalid_and_short_years_and_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="start"):
        replace(period(2024), start=date(2025, 1, 1))
    with pytest.raises(ValueError, match="complete fiscal"):
        replace(period(2024), end=date(2024, 3, 31))
    with pytest.raises(TypeError, match="date"):
        replace(period(2024), start=AT)
    with pytest.raises(ValueError, match="timezone"):
        replace(observation().provenance, retrieved_at=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="retrieved_at"):
        replace(observation().provenance, retrieved_at=None)


def test_missing_publication_is_not_filled_and_known_future_publication_blocks():
    report = report_set()
    item = report.observations[0]
    assert item.provenance.published_at is None
    future = replace(
        item,
        provenance=replace(
            item.provenance,
            published_at=AT + timedelta(days=1),
            retrieved_at=AT + timedelta(days=2),
        ),
    )
    assert "future_evidence" in blocking(
        replace(report, observations=(future,) + report.observations[1:])
    )
    with pytest.raises(ValueError, match="publication"):
        replace(item.provenance, published_at=AT + timedelta(seconds=1))


def test_age_limits_use_business_dates_not_retrieval_and_keep_all_reasons():
    report = report_set()
    assert "share_count_age" not in blocking(report, AT + timedelta(days=119))
    assert "share_count_age" in blocking(report, AT + timedelta(days=120))
    assert "annual_report_age" not in blocking(report, AT + timedelta(days=184))
    assert "annual_report_age" in blocking(report, AT + timedelta(days=185))
    later = AT + timedelta(days=600)
    refreshed = tuple(
        replace(o, provenance=replace(o.provenance, retrieved_at=later))
        for o in report.observations
    )
    reasons = blocking(replace(report, observations=refreshed), later)
    assert {"share_count_age", "bridge_age", "annual_report_age"} <= reasons
    assert "future_observation" in blocking(report, AT - timedelta(days=400))


def test_manual_amendment_keeps_original_and_requires_confirmation():
    report = report_set()
    original = replace(report.observations[0], value=None, status=ValueStatus.MISSING)
    report = replace(report, observations=(original,) + report.observations[1:])
    replacement = replace(
        original,
        value=D(110),
        status=ValueStatus.AVAILABLE,
        provenance=replace(
            original.provenance, kind=EvidenceKind.MANUAL, retrieved_at=None
        ),
    )
    adjustment = ManualAdjustment(
        original, replacement, "synthetic reason", AT, None, None, True
    )
    unconfirmed = replace(report, adjustments=(adjustment,))
    assert unconfirmed.effective_observations[0] is original
    assert "manual_confirmation" in blocking(unconfirmed)
    confirmed = replace(
        unconfirmed,
        adjustments=(
            replace(
                adjustment,
                confirmed_at=AT,
                confirmation_reference="synthetic-confirmation",
            ),
        ),
    )
    assert not blocking(confirmed)
    assert confirmed.effective_observations[0] is replacement
    assert confirmed.observations[0] is original
    assert {
        QualityStatus.MANUALLY_SUPPLEMENTED,
        QualityStatus.BOOK_VALUE_APPROXIMATION,
    } <= {i.status for i in confirmed.issues_at(AT)}
    with pytest.raises(ValueError, match="separate adjustment"):
        replace(report, observations=(replacement,) + report.observations[1:])
    with pytest.raises(ValueError, match="retain field"):
        replace(adjustment, replacement=replace(replacement, period=period(2023)))


def test_imports_are_offline_without_ui_provider_or_network_packages():
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
from portfolio_dashboard.fundamentals import FundamentalRegistry
from portfolio_dashboard.intrinsic_models import IntrinsicValueResult
assert FundamentalRegistry((), ()).report_sets == ()
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_quality_statuses_preserve_simultaneous_blocking_and_advisory_reasons():
    report = report_set()
    report = replace(
        report,
        capital_structure=replace(report.capital_structure, ordinary_class_count=2),
        observations=(),
    )
    issues = report.issues_at(AT + timedelta(days=600))
    assert {
        QualityStatus.UNSUPPORTED_MODEL,
        QualityStatus.INCOMPLETE,
        QualityStatus.STALE,
    } <= {i.status for i in issues}
    assert (
        QualityIssue(QualityStatus.MANUALLY_SUPPLEMENTED, "synthetic").blocking is False
    )


def test_explicit_material_qualifications_block_and_optional_missing_controls_do_not():
    report = report_set()
    optional = replace(
        observation(FundamentalField.PROVIDER_FCF),
        status=ValueStatus.MISSING,
        value=None,
        unit=None,
    )
    assert not blocking(replace(report, observations=report.observations + (optional,)))
    qualified = replace(
        report,
        qualifications=(
            QualityIssue(
                QualityStatus.INCOMPLETE, "synthetic-unresolved-capital-action"
            ),
        ),
    )
    assert "synthetic-unresolved-capital-action" in blocking(qualified)


@pytest.mark.parametrize(
    "when",
    [
        FlowPeriod(
            date(2024, 1, 1), date(2024, 3, 31), PeriodKind.QUARTER, "synthetic-Q1", True
        ),
        FlowPeriod(
            date(2024, 7, 1), date(2025, 6, 30), PeriodKind.TTM, "synthetic-TTM", True
        ),
        period(2025),
    ],
    ids=["quarter", "ttm", "additional-year"],
)
@pytest.mark.parametrize("missing", [False, True], ids=["available", "missing"])
def test_optional_provider_fcf_original_periods_do_not_extend_required_history(
    when, missing
):
    report = report_set()
    control = observation(FundamentalField.PROVIDER_FCF, when=when)
    if missing:
        control = replace(control, status=ValueStatus.MISSING, value=None, unit=None)
    with_control = replace(report, observations=report.observations + (control,))

    assert not blocking(with_control)
    assert with_control.effective_observations[-1] is control
    assert with_control.issues_at(AT) == report.issues_at(AT)


@pytest.mark.parametrize(
    "field",
    [
        FundamentalField.EBIT,
        FundamentalField.OPERATING_TAX_RATE,
        FundamentalField.DEPRECIATION_AMORTIZATION,
        FundamentalField.CAPEX,
    ],
)
@pytest.mark.parametrize(
    "when,reason",
    [
        (
            FlowPeriod(
                date(2025, 1, 1), date(2025, 3, 31),
                PeriodKind.QUARTER, "synthetic-Q1", True,
            ),
            "mixed_flow_periods",
        ),
        (
            FlowPeriod(
                date(2024, 7, 1), date(2025, 6, 30),
                PeriodKind.TTM, "synthetic-TTM", True,
            ),
            "mixed_flow_periods",
        ),
        (period(2025), "historical_components"),
    ],
    ids=["quarter", "ttm", "additional-year"],
)
def test_required_historical_fields_still_enforce_period_consistency(field, when, reason):
    report = report_set()
    value = D("0.2") if field is FundamentalField.OPERATING_TAX_RATE else D(100)
    required_input = observation(field, value, when)
    extended = replace(report, observations=report.observations + (required_input,))
    assert reason in blocking(extended)


def test_optional_provider_fcf_cannot_supply_missing_required_history():
    report = report_set()
    short_history = tuple(o for o in report.observations if o.period != period(2022))
    control = observation(FundamentalField.PROVIDER_FCF, when=period(2022))
    report = replace(report, observations=short_history + (control,))
    assert blocking(report) == {"minimum_history"}


def test_recent_optional_provider_fcf_cannot_refresh_required_annual_report_age():
    report = report_set()
    control = observation(FundamentalField.PROVIDER_FCF, when=period(2025))
    with_control = replace(report, observations=report.observations + (control,))
    stale_at = AT + timedelta(days=185)
    assert "annual_report_age" in blocking(with_control, stale_at)
    assert blocking(with_control, stale_at) == blocking(report, stale_at)


def test_assumptions_references_require_exact_input_revision_class_and_currency():
    from portfolio_dashboard.intrinsic_models import (
        InputReference,
        Scenario,
        ScenarioAssumptions,
    )

    report = report_set()
    reference = InputReference.from_report_set(report)
    assert reference.input_version == report.input_version
    assert reference.report == report.report
    assert reference.instrument_id == report.identity.listing.instrument.instrument_id
    assert reference.share_class_id == report.identity.share_class_id
    plan = ScenarioAssumptions(
        reference,
        "synthetic-plan-v1",
        Scenario.BASE,
        AT,
        "EUR",
        (),
        None,
        None,
        None,
        None,
        None,
        "synthetic rationale",
        None,
        None,
    )
    assert not any(
        i.code == "assumptions_input_mismatch" for i in plan.issues_for(report)
    )
    changed_references = (
        replace(reference, input_version="synthetic-input-v2"),
        replace(reference, share_class_id="synthetic-other-class"),
        replace(reference, report=replace(reference.report, revision="v2")),
    )
    for changed in changed_references:
        assert any(
            i.code == "assumptions_input_mismatch"
            for i in replace(plan, inputs=changed).issues_for(report)
        )
    assert any(
        i.code == "assumptions_input_mismatch"
        for i in replace(plan, currency="USD").issues_for(report)
    )


@pytest.mark.parametrize(
    "kind,status",
    [
        (IssuerKind.BANK, QualityStatus.UNSUPPORTED_MODEL),
        (IssuerKind.INSURER, QualityStatus.UNSUPPORTED_MODEL),
        (IssuerKind.REIT, QualityStatus.UNSUPPORTED_MODEL),
        (IssuerKind.UNKNOWN, QualityStatus.INCOMPLETE),
    ],
)
def test_issuer_model_limits_are_not_overridden_by_verified_mapping(kind, status):
    report = report_set()
    report = replace(report, identity=replace(report.identity, issuer_kind=kind))
    assert any(i.status is status and i.blocking for i in report.issues_at(AT))
