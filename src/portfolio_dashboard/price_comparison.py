"""Pure per-share comparison of the DCF model value with a checked market price.

Builds only on the existing contracts: ``dcf.ScenarioCalculation`` (model value in
reporting-currency major units per share), ``models.Valuation`` (one price
observation with its explicit ``QuoteUnit``), ``valuations.FxRate`` (directed
major-unit rate X -> EUR) and the verified listing registry. Nothing here reads
the clock, a provider, the network, holdings or cost basis; the comparison time is
the explicit ``ComparisonPolicy.as_of``. No financial default is supplied.

Two key figures are kept apart (docs/PRICE_COMPARISON.md):

* discount to the model value   = (1 - price / model value) * 100
* distance from price to model  = (model value / price - 1) * 100

Values are never rounded here; rounding belongs to the display only.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from enum import Enum

from portfolio_dashboard.dcf import ScenarioCalculation, ScenarioValuationSet
from portfolio_dashboard.fundamentals import QualityIssue
from portfolio_dashboard.instruments import EquityForm, VerifiedListing, resolve_listing
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    Valuation,
    ValuationSource,
    _aware,
)
from portfolio_dashboard.valuations import EurValuationStatus, FxRate, unit_price_status

EUR = "EUR"
_HUNDRED = Decimal(100)
_ONE = Decimal(1)

# Limitation codes owned by the comparison (the UI maps them to text). The codes of
# the model calculation itself stay on ``ScenarioCalculation.limitations``.
LIMIT_MODEL_VALUE_NOT_ROLLED_FORWARD = "model_value_as_of_valuation_date_not_rolled_forward"
LIMIT_PER_SHARE_ONLY = "per_share_only_no_quantity_or_cost_basis"


def _context() -> Context:
    return Context(
        prec=50,
        rounding=ROUND_HALF_EVEN,
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


class ComparisonReason(Enum):
    """Why no price comparison (or no EUR figure) exists. Several may coexist."""

    MODEL_VALUE_UNAVAILABLE = "model_value_unavailable"
    MODEL_VALUATION_AFTER_AS_OF = "model_valuation_after_as_of"
    INSTRUMENT_UNVERIFIED = "instrument_unverified"
    INSTRUMENT_MISMATCH = "instrument_mismatch"
    ADR_RATIO_UNSUPPORTED = "adr_ratio_unsupported"
    NOT_COMMON_SHARE = "not_common_share"
    PRICE_MISSING = "price_missing"
    PRICE_INVALID = "price_invalid"
    PRICE_UNIT_UNKNOWN = "price_unit_unknown"
    PRICE_PROVIDER_UNVERIFIED = "price_provider_unverified"
    PRICE_UNIT_MISMATCH = "price_unit_mismatch"
    PRICE_NOT_POSITIVE = "price_not_positive"
    PRICE_TIME_AFTER_AS_OF = "price_time_after_as_of"
    FX_MISSING = "fx_missing"
    FX_DIRECTION_MISMATCH = "fx_direction_mismatch"
    FX_TIME_UNKNOWN = "fx_time_unknown"
    FX_STALE = "fx_stale"
    FX_TIME_AFTER_AS_OF = "fx_time_after_as_of"
    ARITHMETIC_ERROR = "comparison_arithmetic_error"


class ComparisonNote(Enum):
    """Visible, non-blocking facts about the sources of one comparison."""

    PRICE_CACHED = "price_cached_provider_value"
    PRICE_SHEET_REPLACEMENT = "price_sheet_value_not_provider_quote"
    PRICE_TIME_UNKNOWN = "price_time_unknown"
    PRICE_OLDER_THAN_LIMIT = "price_older_than_limit"
    FX_CACHED = "fx_cached_provider_value"
    COMPARED_VIA_EUR = "different_currencies_compared_in_eur"


class PricePosition(Enum):
    """Where the price lies relative to the model value (never a buy/sell advice)."""

    BELOW_MODEL_VALUE = "below_model_value"
    AT_MODEL_VALUE = "at_model_value"
    ABOVE_MODEL_VALUE = "above_model_value"


_PRICE_STATUS_REASONS = {
    EurValuationStatus.MISSING_PRICE: ComparisonReason.PRICE_MISSING,
    EurValuationStatus.INVALID_PRICE: ComparisonReason.PRICE_INVALID,
    EurValuationStatus.UNKNOWN_UNIT: ComparisonReason.PRICE_UNIT_UNKNOWN,
    EurValuationStatus.UNVERIFIED_INSTRUMENT: ComparisonReason.INSTRUMENT_UNVERIFIED,
    EurValuationStatus.UNVERIFIED_PROVIDER: ComparisonReason.PRICE_PROVIDER_UNVERIFIED,
    EurValuationStatus.UNIT_MISMATCH: ComparisonReason.PRICE_UNIT_MISMATCH,
}


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """Explicit comparison time and age limits; the owner chooses the limits.

    ``max_fx_age`` blocks any conversion with an older (or time-less) FX
    observation. ``max_price_age`` only marks an older price visibly: weekends
    and holidays make a hard limit on prices unreliable without a calendar.
    """

    as_of: datetime
    max_fx_age: timedelta
    max_price_age: timedelta

    def __post_init__(self) -> None:
        _aware(self.as_of, "as_of")
        for name in ("max_fx_age", "max_price_age"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError(f"{name} must be a positive timedelta")


@dataclass(frozen=True, slots=True)
class FxCheck:
    """One currency's conversion to EUR: the candidate and why it is (not) usable.

    The candidate stays visible even when it is rejected (wrong direction, too old,
    time unknown); ``rate`` is None only if the caller supplied none.
    """

    currency: str
    rate: FxRate | None
    reason: ComparisonReason | None
    age: timedelta | None

    @property
    def usable(self) -> bool:
        return self.reason is None


@dataclass(frozen=True, slots=True)
class EurAmounts:
    """Per-share EUR display amounts; each is None without a usable path to EUR."""

    model_value: Decimal | None
    target_price: Decimal | None
    price: Decimal | None
    reasons: tuple[ComparisonReason, ...]


@dataclass(frozen=True, slots=True)
class ComparisonSnapshot:
    """The times behind one comparison; unknown times stay None, never defaulted."""

    as_of: datetime
    model_valuation_at: datetime
    price_source: ValuationSource | None
    price_observed_at: datetime | None
    price_retrieved_at: datetime | None
    price_age: timedelta | None


@dataclass(frozen=True, slots=True)
class PriceComparison:
    """Model value, checked price and their relation for one scenario and share.

    ``calculation`` is the unchanged model result with its issues, step trail,
    source references and limitation codes (e.g. terminal_value_dominant), so a
    caller never loses them. Without ``comparison_reasons`` the compared amounts,
    both percentages and ``position`` are set; otherwise they are all None while
    the model value in reporting currency (and any EUR amount that has a usable
    path) stays visible.
    """

    calculation: ScenarioCalculation
    snapshot: ComparisonSnapshot
    reporting_currency: str
    model_value: Decimal | None
    target_price: Decimal | None
    price: Valuation | None
    price_currency: str | None
    price_major: Decimal | None
    comparison_reasons: tuple[ComparisonReason, ...]
    comparison_currency: str | None
    compared_model_value: Decimal | None
    compared_price: Decimal | None
    discount_to_model_percent: Decimal | None
    distance_to_model_percent: Decimal | None
    position: PricePosition | None
    eur: EurAmounts
    fx_checks: tuple[FxCheck, ...]
    notes: tuple[ComparisonNote, ...]
    warnings: tuple[DataWarning, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        figures = (
            self.comparison_currency,
            self.compared_model_value,
            self.compared_price,
            self.discount_to_model_percent,
            self.distance_to_model_percent,
            self.position,
        )
        if self.comparison_reasons:
            if any(figure is not None for figure in figures):
                raise ValueError("a blocked comparison cannot expose key figures")
        elif any(figure is None for figure in figures):
            raise ValueError("a comparable result needs all key figures")

    @property
    def comparable(self) -> bool:
        return not self.comparison_reasons

    @property
    def scenario(self) -> Scenario:
        return self.calculation.result.scenario

    @property
    def model_issues(self) -> tuple[QualityIssue, ...]:
        return self.calculation.result.issues


@dataclass(frozen=True, slots=True)
class ScenarioPriceComparisons:
    """One comparison per supplied scenario in the caller's order, same snapshot."""

    comparisons: tuple[PriceComparison, ...]
    missing: tuple[Scenario, ...]
    limitations: tuple[str, ...]

    def get(self, scenario: Scenario) -> PriceComparison | None:
        return next((c for c in self.comparisons if c.scenario is scenario), None)


def _fx_check(
    currency: str,
    candidates: Mapping[str, FxRate],
    policy: ComparisonPolicy,
) -> FxCheck:
    rate = candidates.get(currency)
    if rate is None:
        return FxCheck(currency, None, ComparisonReason.FX_MISSING, None)
    if (rate.base_currency, rate.quote_currency) != (currency, EUR):
        # Directed major-unit rate: the opposite direction is never inverted.
        return FxCheck(currency, rate, ComparisonReason.FX_DIRECTION_MISMATCH, None)
    observed = rate.provenance.observed_at
    if observed is None:
        return FxCheck(currency, rate, ComparisonReason.FX_TIME_UNKNOWN, None)
    if observed > policy.as_of or rate.provenance.retrieved_at > policy.as_of:
        return FxCheck(currency, rate, ComparisonReason.FX_TIME_AFTER_AS_OF, None)
    age = policy.as_of - observed
    if age > policy.max_fx_age:
        return FxCheck(currency, rate, ComparisonReason.FX_STALE, age)
    return FxCheck(currency, rate, None, age)


def _to_eur(amount: Decimal | None, currency: str, checks: dict) -> Decimal | None:
    if amount is None:
        return None
    if currency == EUR:
        return amount
    check = checks.get(currency)
    if check is None or not check.usable:
        return None
    return amount * check.rate.rate


def compare_to_price(
    calculation: ScenarioCalculation,
    instrument: Instrument,
    price: Valuation | None,
    listings: tuple[VerifiedListing, ...],
    fx_rates: Mapping[str, FxRate] | None,
    policy: ComparisonPolicy,
) -> PriceComparison:
    """Compare one scenario's per-share model value with one checked price.

    ``instrument`` is the listing the ``price`` belongs to. It must resolve in the
    verified registry to the very instrument ID the model was calculated for;
    other IDs, ADRs and non-stock products are refused (ADR conversion ratios are
    not part of any contract). ``fx_rates`` maps a currency to its directed
    ``FxRate`` to EUR, like ``value_portfolio``. Equal reporting and quote
    currencies are compared directly (FX does not change the ratios); different
    ones only through usable EUR conversions of both sides.
    """
    if not isinstance(calculation, ScenarioCalculation):
        raise TypeError("calculation must be ScenarioCalculation")
    if not isinstance(instrument, Instrument):
        raise TypeError("instrument must be Instrument")
    if price is not None and not isinstance(price, Valuation):
        raise TypeError("price must be Valuation or None")
    if not isinstance(policy, ComparisonPolicy):
        raise TypeError("policy must be ComparisonPolicy")
    candidates = {} if fx_rates is None else fx_rates
    if not all(isinstance(rate, FxRate) for rate in candidates.values()):
        raise TypeError("fx_rates must map currencies to FxRate")

    result = calculation.result
    reporting = result.currency
    reasons: list[ComparisonReason] = []
    notes: list[ComparisonNote] = []

    # Model side: stays visible in reporting currency whatever happens to the price.
    model = result.value_per_share
    if model is None or model <= 0:
        reasons.append(ComparisonReason.MODEL_VALUE_UNAVAILABLE)
        model = None
    if result.valuation_at > policy.as_of:
        reasons.append(ComparisonReason.MODEL_VALUATION_AFTER_AS_OF)
    target = result.target_price if model is not None else None

    # Identity: the price must belong to the same share of the same security.
    listing = resolve_listing(instrument, listings)
    identity_ok = False
    if listing is None:
        reasons.append(ComparisonReason.INSTRUMENT_UNVERIFIED)
    elif listing.instrument.instrument_id != result.inputs.instrument_id:
        reasons.append(ComparisonReason.INSTRUMENT_MISMATCH)
    elif listing.instrument.instrument_type is not InstrumentType.STOCK:
        reasons.append(ComparisonReason.NOT_COMMON_SHARE)
    elif listing.equity_form is not EquityForm.SHARE:
        reasons.append(ComparisonReason.ADR_RATIO_UNSUPPORTED)
    else:
        identity_ok = True

    # Price side, only for a price of the verified same share.
    shown_price = None
    price_currency = price_major = None
    price_observed = price_retrieved = price_age = price_source = None
    if identity_ok:
        status = unit_price_status(price, listing)
        if status is not None:
            reasons.append(_PRICE_STATUS_REASONS[status])
        elif price.price <= 0:
            reasons.append(ComparisonReason.PRICE_NOT_POSITIVE)
        else:
            shown_price = price
            price_currency = price.unit.currency
            provenance = price.provenance
            price_source = provenance.source
            price_observed = provenance.observed_at
            price_retrieved = provenance.retrieved_at
            try:
                with localcontext(_context()):
                    price_major = price.price * price.unit.scale_to_currency
            except DecimalException:
                reasons.append(ComparisonReason.ARITHMETIC_ERROR)
            if price_source is ValuationSource.CACHED_PROVIDER:
                notes.append(ComparisonNote.PRICE_CACHED)
            elif price_source is ValuationSource.SHEET:
                notes.append(ComparisonNote.PRICE_SHEET_REPLACEMENT)
            if price_observed is None:
                notes.append(ComparisonNote.PRICE_TIME_UNKNOWN)
            elif price_observed > policy.as_of or price_retrieved > policy.as_of:
                reasons.append(ComparisonReason.PRICE_TIME_AFTER_AS_OF)
            else:
                price_age = policy.as_of - price_observed
                if price_age > policy.max_price_age:
                    notes.append(ComparisonNote.PRICE_OLDER_THAN_LIMIT)

    # FX: only for currencies that actually take part in a conversion.
    sides = []
    if model is not None:
        sides.append(reporting)
    if price_major is not None:
        sides.append(price_currency)
    checks = {
        currency: _fx_check(currency, candidates, policy)
        for currency in dict.fromkeys(sides)
        if currency != EUR
    }
    eur_model = eur_target = eur_price = None
    try:
        with localcontext(_context()):
            eur_model = _to_eur(model, reporting, checks)
            eur_target = _to_eur(target, reporting, checks)
            eur_price = _to_eur(price_major, price_currency, checks)
    except DecimalException:
        eur_model = eur_target = eur_price = None
        reasons.append(ComparisonReason.ARITHMETIC_ERROR)
    eur_reasons = tuple(
        dict.fromkeys(c.reason for c in checks.values() if c.reason is not None)
    )
    if any(
        c.usable and c.rate.provenance.source is ValuationSource.CACHED_PROVIDER
        for c in checks.values()
    ):
        notes.append(ComparisonNote.FX_CACHED)

    # The comparison itself: directly in one shared currency, else both sides in EUR.
    comparison_currency = compared_model = compared_price = None
    discount = distance = position = None
    if model is not None and price_major is not None and not reasons:
        if price_currency == reporting:
            comparison_currency = reporting
            compared_model, compared_price = model, price_major
        elif eur_model is None or eur_price is None:
            reasons.extend(eur_reasons)
        else:
            comparison_currency = EUR
            compared_model, compared_price = eur_model, eur_price
            notes.append(ComparisonNote.COMPARED_VIA_EUR)
    if comparison_currency is not None:
        try:
            with localcontext(_context()):
                discount = (_ONE - compared_price / compared_model) * _HUNDRED
                distance = (compared_model / compared_price - _ONE) * _HUNDRED
        except DecimalException:
            reasons.append(ComparisonReason.ARITHMETIC_ERROR)
            if ComparisonNote.COMPARED_VIA_EUR in notes:
                notes.remove(ComparisonNote.COMPARED_VIA_EUR)
            comparison_currency = compared_model = compared_price = None
            discount = distance = None
        else:
            if compared_price < compared_model:
                position = PricePosition.BELOW_MODEL_VALUE
            elif compared_price > compared_model:
                position = PricePosition.ABOVE_MODEL_VALUE
            else:
                position = PricePosition.AT_MODEL_VALUE

    warnings = []
    if shown_price is not None:
        warnings.extend(shown_price.warnings)
    for check in checks.values():
        if check.rate is not None:
            warnings.extend(check.rate.warnings)
    limitations = tuple(
        dict.fromkeys(
            calculation.limitations
            + (LIMIT_MODEL_VALUE_NOT_ROLLED_FORWARD, LIMIT_PER_SHARE_ONLY)
        )
    )
    return PriceComparison(
        calculation,
        ComparisonSnapshot(
            policy.as_of,
            result.valuation_at,
            price_source,
            price_observed,
            price_retrieved,
            price_age,
        ),
        reporting,
        model,
        target,
        shown_price,
        price_currency,
        price_major,
        tuple(dict.fromkeys(reasons)),
        comparison_currency,
        compared_model,
        compared_price,
        discount,
        distance,
        position,
        EurAmounts(eur_model, eur_target, eur_price, eur_reasons),
        tuple(checks.values()),
        tuple(dict.fromkeys(notes)),
        tuple(dict.fromkeys(warnings)),
        limitations,
    )


def compare_scenarios_to_price(
    valuation_set: ScenarioValuationSet,
    instrument: Instrument,
    price: Valuation | None,
    listings: tuple[VerifiedListing, ...],
    fx_rates: Mapping[str, FxRate] | None,
    policy: ComparisonPolicy,
) -> ScenarioPriceComparisons:
    """Compare every scenario of a set with the same price, FX and time.

    Order and labels are kept as supplied; a blocked scenario does not block
    another. All members share one price observation, one FX selection and one
    ``as_of``, so their distances rest on the same snapshot.
    """
    if not isinstance(valuation_set, ScenarioValuationSet):
        raise TypeError("valuation_set must be ScenarioValuationSet")
    comparisons = tuple(
        compare_to_price(c, instrument, price, listings, fx_rates, policy)
        for c in valuation_set.calculations
    )
    return ScenarioPriceComparisons(
        comparisons, valuation_set.missing, valuation_set.limitations
    )
