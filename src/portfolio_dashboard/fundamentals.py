"""Immutable offline fundamental inputs, identity evidence and structural checks.

Constructing verified records asserts an external check; it does not perform it.
Real evidence belongs in protected runtime storage. No symbol search, provider
access, financial normalization, FCFF calculation or personal holdings live here.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.instruments import EquityForm, VerifiedListing, resolve_listing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    QuoteUnit,
    ValueStatus,
    _aware,
    _decimal,
    _text,
)


def _date(value: date, name: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{name} must be a date, not a timestamp")


def _tuple(value: tuple, kind: type, name: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(v, kind) for v in value):
        raise TypeError(f"{name} must be a tuple of {kind.__name__}")


class QualityStatus(Enum):
    MISSING = "missing"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    MANUALLY_SUPPLEMENTED = "manually_supplemented"
    UNCONFIRMED_ASSUMPTIONS = "unconfirmed_assumptions"
    UNSUPPORTED_MODEL = "unsupported_model"
    UNKNOWN_PUBLICATION = "unknown_publication"
    BOOK_VALUE_APPROXIMATION = "book_value_approximation"


@dataclass(frozen=True, slots=True)
class QualityIssue:
    status: QualityStatus
    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, QualityStatus):
            raise TypeError("status must be QualityStatus")
        _text(self.code, "code")

    @property
    def blocking(self) -> bool:
        return self.status not in {
            QualityStatus.MANUALLY_SUPPLEMENTED,
            QualityStatus.UNKNOWN_PUBLICATION,
            QualityStatus.BOOK_VALUE_APPROXIMATION,
        }


class IssuerKind(Enum):
    NONFINANCIAL = "nonfinancial"
    BANK = "bank"
    INSURER = "insurer"
    REIT = "reit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FundamentalSource:
    provider: str
    entity_id: str

    def __post_init__(self) -> None:
        _text(self.provider, "provider")
        _text(self.entity_id, "entity_id")


@dataclass(frozen=True, slots=True)
class VerifiedFundamentalIdentity:
    listing: VerifiedListing
    share_class_id: str
    issuer_id: str
    source: FundamentalSource
    consolidation_scope: str
    issuer_kind: IssuerKind
    evidence_reference: str
    verified_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.listing, VerifiedListing):
            raise TypeError("listing must be VerifiedListing")
        if not isinstance(self.source, FundamentalSource):
            raise TypeError("source must be FundamentalSource")
        if not isinstance(self.issuer_kind, IssuerKind):
            raise TypeError("issuer_kind must be IssuerKind")
        for name in (
            "share_class_id",
            "issuer_id",
            "consolidation_scope",
            "evidence_reference",
        ):
            _text(getattr(self, name), name)
        _aware(self.verified_at, "verified_at")

    @property
    def data_key(self) -> tuple:
        # An issuer/source match alone never substitutes another security/class.
        return (
            self.listing.instrument.instrument_id,
            self.share_class_id,
            self.issuer_id,
            self.source,
            self.consolidation_scope,
        )

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        if (
            self.listing.instrument.instrument_type is not InstrumentType.STOCK
            or self.listing.equity_form is not EquityForm.SHARE
            or self.issuer_kind
            in {IssuerKind.BANK, IssuerKind.INSURER, IssuerKind.REIT}
        ):
            return (QualityIssue(QualityStatus.UNSUPPORTED_MODEL, "instrument_model"),)
        if self.issuer_kind is IssuerKind.UNKNOWN:
            return (QualityIssue(QualityStatus.INCOMPLETE, "issuer_kind_unknown"),)
        return ()


@dataclass(frozen=True, slots=True)
class CapitalStructure:
    """Complete issuer-level ordinary-class count, including unlisted classes.

    None denotes no complete proof. A listing count is never such a proof.
    """

    issuer_id: str
    source: FundamentalSource
    as_of: date
    ordinary_class_count: int | None
    includes_unlisted: bool
    evidence_reference: str | None
    verified_at: datetime | None

    def __post_init__(self) -> None:
        _text(self.issuer_id, "issuer_id")
        if not isinstance(self.source, FundamentalSource):
            raise TypeError("source must be FundamentalSource")
        _date(self.as_of, "as_of")
        if type(self.includes_unlisted) is not bool:
            raise TypeError("includes_unlisted must be bool")
        if self.evidence_reference is not None:
            _text(self.evidence_reference, "evidence_reference")
        if self.verified_at is not None:
            _aware(self.verified_at, "verified_at")
        if self.ordinary_class_count is not None:
            if (
                type(self.ordinary_class_count) is not int
                or self.ordinary_class_count < 1
            ):
                raise ValueError("ordinary_class_count must be a positive integer")
            if not self.includes_unlisted or not self.evidence_reference:
                raise ValueError(
                    "class count requires complete evidence incl. unlisted"
                )
            if self.verified_at is None:
                raise ValueError("class count requires verification time")

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        if self.ordinary_class_count is None:
            return (
                QualityIssue(QualityStatus.INCOMPLETE, "ordinary_class_count_unknown"),
            )
        if self.ordinary_class_count != 1:
            return (QualityIssue(QualityStatus.UNSUPPORTED_MODEL, "multiple_classes"),)
        return ()


class UnitKind(Enum):
    MONEY = "money"
    SHARES = "shares"
    RATE = "rate"


@dataclass(frozen=True, slots=True)
class FundamentalUnit:
    kind: UnitKind
    scale: Decimal | None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, UnitKind):
            raise TypeError("kind must be UnitKind")
        if self.currency is not None:
            if self.kind is not UnitKind.MONEY:
                raise ValueError("only money has a currency")
            QuoteUnit(self.currency, None)  # Same explicit currency syntax as prices.
        if self.scale is not None:
            _decimal(self.scale, "scale")
            if self.scale <= 0:
                raise ValueError("scale must be positive")
        if self.kind is UnitKind.RATE and self.scale != Decimal(1):
            raise ValueError("rates are unscaled dimensionless fractions")

    @property
    def known(self) -> bool:
        return self.scale is not None and (
            self.kind is not UnitKind.MONEY or self.currency is not None
        )


class PeriodKind(Enum):
    FISCAL_YEAR = "fiscal_year"
    QUARTER = "quarter"
    YTD = "ytd"
    TTM = "ttm"


@dataclass(frozen=True, slots=True)
class FlowPeriod:
    start: date
    end: date
    kind: PeriodKind
    fiscal_label: str
    complete: bool

    def __post_init__(self) -> None:
        _date(self.start, "start")
        _date(self.end, "end")
        if self.start > self.end:
            raise ValueError("period start must not follow end")
        if not isinstance(self.kind, PeriodKind):
            raise TypeError("kind must be PeriodKind")
        _text(self.fiscal_label, "fiscal_label")
        if type(self.complete) is not bool:
            raise TypeError("complete must be bool")
        if (
            self.kind is PeriodKind.FISCAL_YEAR
            and self.complete
            and not 364 <= (self.end - self.start).days + 1 <= 371
        ):
            raise ValueError("complete fiscal year must span 52/53 weeks or a year")


@dataclass(frozen=True, slots=True)
class InstantPeriod:
    as_of: date

    def __post_init__(self) -> None:
        _date(self.as_of, "as_of")


@dataclass(frozen=True, slots=True)
class CurrentSharesPeriod:
    as_of: date
    split_basis: str

    def __post_init__(self) -> None:
        _date(self.as_of, "as_of")
        _text(self.split_basis, "split_basis")


@dataclass(frozen=True, slots=True)
class WeightedSharesPeriod:
    period: FlowPeriod
    split_basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.period, FlowPeriod):
            raise TypeError("period must be FlowPeriod")
        _text(self.split_basis, "split_basis")


class FundamentalField(Enum):
    EBIT = "ebit"
    OPERATING_TAX_RATE = "operating_tax_rate"
    DEPRECIATION_AMORTIZATION = "depreciation_amortization"
    CAPEX = "capex"
    OPERATING_NWC = "operating_nwc"
    CHANGE_OPERATING_NWC = "change_operating_nwc"
    PROVIDER_FCF = "provider_fcf"  # Control only; never FCFF.
    EXCESS_CASH = "excess_cash"
    NONOPERATING_ASSETS = "nonoperating_assets"
    FINANCIAL_DEBT = "financial_debt"
    PREFERRED_CLAIMS = "preferred_claims"
    MINORITY_CLAIMS = "minority_claims"
    OUTSTANDING_SHARES = "outstanding_shares"
    INCREMENTAL_DILUTION = "incremental_dilution"
    WEIGHTED_EPS_SHARES = "weighted_eps_shares"


FLOW_FIELDS = frozenset(
    {
        FundamentalField.EBIT,
        FundamentalField.OPERATING_TAX_RATE,
        FundamentalField.DEPRECIATION_AMORTIZATION,
        FundamentalField.CAPEX,
        FundamentalField.CHANGE_OPERATING_NWC,
        FundamentalField.PROVIDER_FCF,
    }
)
BRIDGE_FIELDS = frozenset(
    {
        FundamentalField.EXCESS_CASH,
        FundamentalField.NONOPERATING_ASSETS,
        FundamentalField.FINANCIAL_DEBT,
        FundamentalField.PREFERRED_CLAIMS,
        FundamentalField.MINORITY_CLAIMS,
    }
)
CURRENT_SHARE_FIELDS = frozenset(
    {FundamentalField.OUTSTANDING_SHARES, FundamentalField.INCREMENTAL_DILUTION}
)


class EvidenceKind(Enum):
    PROVIDER = "provider"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    source: FundamentalSource
    kind: EvidenceKind
    field_reference: str
    raw_definition: str
    normalization: str
    retrieved_at: datetime | None
    reported_at: datetime | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, FundamentalSource):
            raise TypeError("source must be FundamentalSource")
        if not isinstance(self.kind, EvidenceKind):
            raise TypeError("kind must be EvidenceKind")
        for name in ("field_reference", "raw_definition", "normalization"):
            _text(getattr(self, name), name)
        for name in ("retrieved_at", "reported_at", "published_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
        if self.kind is EvidenceKind.PROVIDER and self.retrieved_at is None:
            raise ValueError("provider evidence requires retrieved_at")
        if (
            self.retrieved_at is not None
            and self.published_at is not None
            and self.published_at > self.retrieved_at
        ):
            raise ValueError("publication cannot follow retrieval")


@dataclass(frozen=True, slots=True)
class ReportIdentity:
    issuer_id: str
    report_set_id: str
    revision: str
    consolidation_scope: str

    def __post_init__(self) -> None:
        for name in ("issuer_id", "report_set_id", "revision", "consolidation_scope"):
            _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class FundamentalObservation:
    field: FundamentalField
    value: Decimal | None
    status: ValueStatus
    unit: FundamentalUnit | None
    period: FlowPeriod | InstantPeriod | CurrentSharesPeriod | WeightedSharesPeriod
    report: ReportIdentity
    provenance: FieldProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.field, FundamentalField):
            raise TypeError("field must be FundamentalField")
        if not isinstance(self.status, ValueStatus):
            raise TypeError("status must be ValueStatus")
        if not isinstance(self.report, ReportIdentity):
            raise TypeError("report must be ReportIdentity")
        if not isinstance(self.provenance, FieldProvenance):
            raise TypeError("provenance must be FieldProvenance")
        period_type = (
            FlowPeriod
            if self.field in FLOW_FIELDS
            else CurrentSharesPeriod
            if self.field in CURRENT_SHARE_FIELDS
            else WeightedSharesPeriod
            if self.field is FundamentalField.WEIGHTED_EPS_SHARES
            else InstantPeriod
        )
        if not isinstance(self.period, period_type):
            raise TypeError(f"{self.field.value} requires {period_type.__name__}")
        unit_kind = (
            UnitKind.RATE
            if self.field is FundamentalField.OPERATING_TAX_RATE
            else UnitKind.SHARES
            if self.field in CURRENT_SHARE_FIELDS
            or self.field is FundamentalField.WEIGHTED_EPS_SHARES
            else UnitKind.MONEY
        )
        if self.unit is not None:
            if not isinstance(self.unit, FundamentalUnit):
                raise TypeError("unit must be FundamentalUnit or None")
            if self.unit.kind is not unit_kind:
                raise ValueError("field has contradictory unit kind")
        if self.status is not ValueStatus.AVAILABLE:
            if self.value is not None:
                raise ValueError("missing/invalid observations have no usable number")
            return
        _decimal(self.value, "value")
        if self.field is FundamentalField.OPERATING_TAX_RATE:
            if not 0 <= self.value < 1:
                raise ValueError("operating tax rate must be in [0, 1)")
        elif self.field in {
            FundamentalField.OUTSTANDING_SHARES,
            FundamentalField.WEIGHTED_EPS_SHARES,
        }:
            if self.value <= 0:
                raise ValueError("share count must be positive")
        elif (
            self.field
            not in {
                FundamentalField.EBIT,
                FundamentalField.OPERATING_NWC,
                FundamentalField.CHANGE_OPERATING_NWC,
                FundamentalField.PROVIDER_FCF,
            }
            and self.value < 0
        ):
            raise ValueError("normalized field must be nonnegative")

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        issues = []
        if self.status is not ValueStatus.AVAILABLE:
            status = (
                QualityStatus.MISSING
                if self.status is ValueStatus.MISSING
                else QualityStatus.INVALID
            )
            issues.append(QualityIssue(status, self.field.value))
        if self.unit is None or not self.unit.known:
            issues.append(QualityIssue(QualityStatus.INCOMPLETE, "unknown_unit"))
        if self.provenance.published_at is None:
            issues.append(
                QualityIssue(QualityStatus.UNKNOWN_PUBLICATION, self.field.value)
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class ManualAdjustment:
    """Separate amendment; original evidence is retained, never overwritten."""

    original: FundamentalObservation
    replacement: FundamentalObservation
    reason: str
    changed_at: datetime
    confirmed_at: datetime | None
    confirmation_reference: str | None
    book_value_approximation: bool = False

    def __post_init__(self) -> None:
        for name in ("original", "replacement"):
            if not isinstance(getattr(self, name), FundamentalObservation):
                raise TypeError(f"{name} must be FundamentalObservation")
        if (self.original.field, self.original.period, self.original.report) != (
            self.replacement.field,
            self.replacement.period,
            self.replacement.report,
        ):
            raise ValueError("adjustment must retain field, period and report identity")
        if self.replacement.provenance.kind is not EvidenceKind.MANUAL:
            raise ValueError("replacement must carry manual evidence")
        _text(self.reason, "reason")
        _aware(self.changed_at, "changed_at")
        if (self.confirmed_at is None) != (self.confirmation_reference is None):
            raise ValueError("confirmation requires both time and reference")
        if self.confirmed_at is not None:
            _aware(self.confirmed_at, "confirmed_at")
            _text(self.confirmation_reference, "confirmation_reference")
            if self.confirmed_at < self.changed_at:
                raise ValueError("confirmation cannot precede change")
        if type(self.book_value_approximation) is not bool:
            raise TypeError("book_value_approximation must be bool")


@dataclass(frozen=True, slots=True)
class FundamentalReportSet:
    """One selected, consistent input version; no calculated FCFF is stored.

    Structural checks do not establish economic suitability or calculate cash
    flows; the later calculator must also validate financial/model conditions.
    """

    identity: VerifiedFundamentalIdentity
    report: ReportIdentity
    input_version: str
    currency: str
    observations: tuple[FundamentalObservation, ...]
    capital_structure: CapitalStructure
    bridge_date: date
    shares_period: CurrentSharesPeriod
    adjustments: tuple[ManualAdjustment, ...] = ()
    qualifications: tuple[QualityIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VerifiedFundamentalIdentity):
            raise TypeError("identity must be VerifiedFundamentalIdentity")
        if not isinstance(self.report, ReportIdentity):
            raise TypeError("report must be ReportIdentity")
        if not isinstance(self.capital_structure, CapitalStructure):
            raise TypeError("capital_structure must be CapitalStructure")
        if not isinstance(self.shares_period, CurrentSharesPeriod):
            raise TypeError("shares_period must be CurrentSharesPeriod")
        _text(self.input_version, "input_version")
        QuoteUnit(self.currency, Decimal(1))
        _date(self.bridge_date, "bridge_date")
        _tuple(self.observations, FundamentalObservation, "observations")
        _tuple(self.adjustments, ManualAdjustment, "adjustments")
        _tuple(self.qualifications, QualityIssue, "qualifications")
        if (
            self.report.issuer_id != self.identity.issuer_id
            or self.report.consolidation_scope != self.identity.consolidation_scope
            or self.capital_structure.issuer_id != self.identity.issuer_id
        ):
            raise ValueError("report/identity/capital structure do not match")
        if self.capital_structure.as_of != self.shares_period.as_of:
            raise ValueError("class proof must match current capital date")
        keys = set()
        for observation in self.observations:
            self._check_observation(observation)
            key = (observation.field, observation.period)
            if key in keys:
                raise ValueError("duplicate field/period in report set")
            keys.add(key)
        replaced = set()
        for adjustment in self.adjustments:
            if adjustment.original not in self.observations:
                raise ValueError("adjustment original must belong to selected input")
            self._check_observation(adjustment.replacement)
            key = (adjustment.original.field, adjustment.original.period)
            if key in replaced:
                raise ValueError("multiple adjustments require a new input version")
            replaced.add(key)

    def _check_observation(self, observation: FundamentalObservation) -> None:
        if observation.report != self.report:
            raise ValueError("mixed reports or restatement revisions")
        if (
            observation.provenance.kind is EvidenceKind.PROVIDER
            and observation.provenance.source != self.identity.source
        ):
            raise ValueError("observation has an unverified fundamental source")
        if (
            observation.unit is not None
            and observation.unit.kind is UnitKind.MONEY
            and observation.unit.currency not in (None, self.currency)
        ):
            raise ValueError("mixed reporting currencies")
        if (
            observation.field in BRIDGE_FIELDS
            and observation.period.as_of != self.bridge_date
        ):
            raise ValueError("mixed bridge dates")
        if (
            observation.field in CURRENT_SHARE_FIELDS
            and observation.period != self.shares_period
        ):
            raise ValueError("mixed current share dates or split bases")
        if observation.provenance.kind is EvidenceKind.MANUAL:
            # Manual values enter through amendments with explicit confirmation.
            if not any(a.replacement == observation for a in self.adjustments):
                raise ValueError("manual observation requires a separate adjustment")

    @property
    def effective_observations(self) -> tuple[FundamentalObservation, ...]:
        replacements = {
            a.original: a.replacement
            for a in self.adjustments
            if a.confirmed_at is not None
        }
        return tuple(replacements.get(o, o) for o in self.observations)

    def issues_at(self, valuation_at: datetime) -> tuple[QualityIssue, ...]:
        """Deterministic structural/age gate, with all independent reasons kept."""
        _aware(valuation_at, "valuation_at")
        day = valuation_at.astimezone(timezone.utc).date()
        issues = list(
            self.identity.issues + self.capital_structure.issues + self.qualifications
        )
        observations = self.effective_observations
        for observation in observations:
            # Optional controls do not turn an otherwise complete basis missing.
            if observation.field not in {
                FundamentalField.PROVIDER_FCF,
                FundamentalField.WEIGHTED_EPS_SHARES,
            }:
                issues.extend(observation.issues)
            provenance = observation.provenance
            if any(
                t is not None and t > valuation_at
                for t in (
                    provenance.reported_at,
                    provenance.published_at,
                    provenance.retrieved_at,
                )
            ):
                issues.append(QualityIssue(QualityStatus.INVALID, "future_evidence"))
            period = observation.period
            end = (
                period.end
                if isinstance(period, FlowPeriod)
                else period.period.end
                if isinstance(period, WeightedSharesPeriod)
                else period.as_of
            )
            if end > day:
                issues.append(QualityIssue(QualityStatus.INVALID, "future_observation"))
        periods = sorted(
            {o.period for o in observations if isinstance(o.period, FlowPeriod)},
            key=lambda p: p.start,
        )
        annual = [p for p in periods if p.kind is PeriodKind.FISCAL_YEAR and p.complete]
        if len(annual) < 3:
            issues.append(QualityIssue(QualityStatus.INCOMPLETE, "minimum_history"))
        if len(annual) != len(periods) or any(
            a.end + timedelta(days=1) != b.start or a.fiscal_label == b.fiscal_label
            for a, b in zip(annual, annual[1:])
        ):
            issues.append(QualityIssue(QualityStatus.INVALID, "mixed_flow_periods"))
        keys = {(o.field, o.period) for o in observations}
        historical = FLOW_FIELDS - {
            FundamentalField.PROVIDER_FCF,
            FundamentalField.CHANGE_OPERATING_NWC,
        }
        for period in annual:
            required = {(f, period) for f in historical}
            # Retain opening and closing balance evidence for the later NWC check.
            required |= {
                (FundamentalField.OPERATING_NWC, InstantPeriod(d))
                for d in (period.start - timedelta(days=1), period.end)
            }
            if not required <= keys:
                issues.append(
                    QualityIssue(QualityStatus.INCOMPLETE, "historical_components")
                )
        if not {(f, InstantPeriod(self.bridge_date)) for f in BRIDGE_FIELDS} <= keys:
            issues.append(QualityIssue(QualityStatus.INCOMPLETE, "capital_bridge"))
        if not {(f, self.shares_period) for f in CURRENT_SHARE_FIELDS} <= keys:
            issues.append(QualityIssue(QualityStatus.INCOMPLETE, "current_shares"))
        dated = [
            (self.bridge_date, 550, "bridge_age"),
            (self.shares_period.as_of, 120, "share_count_age"),
        ]
        if annual:
            dated.append((annual[-1].end, 550, "annual_report_age"))
        for as_of, limit, code in dated:
            age = (day - as_of).days
            if age < 0:
                issues.append(QualityIssue(QualityStatus.INVALID, code))
            elif age > limit:
                issues.append(QualityIssue(QualityStatus.STALE, code))
        if any(
            t is not None and t > valuation_at
            for t in (self.identity.verified_at, self.capital_structure.verified_at)
        ):
            issues.append(
                QualityIssue(QualityStatus.INVALID, "future_identity_evidence")
            )
        for adjustment in self.adjustments:
            issues.append(
                QualityIssue(
                    QualityStatus.MANUALLY_SUPPLEMENTED,
                    adjustment.replacement.field.value,
                )
            )
            if adjustment.confirmed_at is None:
                issues.append(
                    QualityIssue(
                        QualityStatus.UNCONFIRMED_ASSUMPTIONS, "manual_confirmation"
                    )
                )
            if adjustment.changed_at > valuation_at or (
                adjustment.confirmed_at is not None
                and adjustment.confirmed_at > valuation_at
            ):
                issues.append(QualityIssue(QualityStatus.INVALID, "future_adjustment"))
            if adjustment.book_value_approximation:
                issues.append(
                    QualityIssue(
                        QualityStatus.BOOK_VALUE_APPROXIMATION,
                        adjustment.replacement.field.value,
                    )
                )
        return tuple(dict.fromkeys(issues))


@dataclass(frozen=True, slots=True)
class FundamentalRegistry:
    """Explicit selected data shared by callers in portfolio and watchlist."""

    identities: tuple[VerifiedFundamentalIdentity, ...]
    report_sets: tuple[FundamentalReportSet, ...]

    def __post_init__(self) -> None:
        _tuple(self.identities, VerifiedFundamentalIdentity, "identities")
        _tuple(self.report_sets, FundamentalReportSet, "report_sets")
        listings = tuple(i.listing for i in self.identities)
        # Reuse existing security/listing collision and metadata validation.
        if listings:
            resolve_listing(listings[0].instrument, listings)
        securities, sources, classes, issuers = {}, {}, {}, {}
        for identity in self.identities:
            instrument_id = identity.listing.instrument.instrument_id
            prior = securities.setdefault(instrument_id, identity.data_key)
            if prior != identity.data_key:
                raise ValueError("conflicting share class or issuer for instrument")
            source_identity = (identity.issuer_id, identity.consolidation_scope)
            if sources.setdefault(identity.source, source_identity) != source_identity:
                raise ValueError("fundamental source assigned to conflicting issuer")
            class_identity = (identity.issuer_id, identity.listing.equity_form)
            if (
                classes.setdefault(identity.share_class_id, class_identity)
                != class_identity
            ):
                raise ValueError("share class assigned to conflicting issuer or form")
            if (
                issuers.setdefault(identity.issuer_id, identity.issuer_kind)
                is not identity.issuer_kind
            ):
                raise ValueError("conflicting issuer kind")
        selected = set()
        for report_set in self.report_sets:
            key = report_set.identity.data_key
            if report_set.identity not in self.identities:
                raise ValueError("report set requires a verified registry identity")
            if key in selected:
                raise ValueError(
                    "only one selected input version per verified security"
                )
            selected.add(key)

    def resolve(self, instrument: Instrument) -> FundamentalReportSet | None:
        listing = resolve_listing(instrument, tuple(i.listing for i in self.identities))
        if listing is None:
            return None
        identity = next(i for i in self.identities if i.listing is listing)
        return next(
            (r for r in self.report_sets if r.identity.data_key == identity.data_key),
            None,
        )
