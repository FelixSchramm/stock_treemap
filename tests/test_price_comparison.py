"""Price/model comparison checks with invented numbers and hand-derived expectations.

Expected values are derived by hand (80 against 100 -> 20 % and 25 %, 70 against
80 -> 12,5 % ...) or with exact ``Fraction`` arithmetic; none repeats the
implementation's formula chain with the implementation's own helpers.
"""

import inspect
import re
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from test_dcf import DOC_PER_SHARE, base_report, doc_plan, doc_plans
from test_fundamentals import AT, REPORT

import portfolio_dashboard.price_comparison as price_comparison
from portfolio_dashboard.dcf import (
    LIMIT_TERMINAL_DOMINANT,
    ScenarioCalculation,
    calculate_scenario,
    calculate_scenarios,
)
from portfolio_dashboard.fundamentals import QualityIssue, QualityStatus
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.intrinsic_models import (
    CalculationStep,
    InputReference,
    IntrinsicValueResult,
    Scenario,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_comparison import (
    ComparisonNote,
    ComparisonPolicy,
    ComparisonReason,
    PricePosition,
    compare_scenarios_to_price,
    compare_to_price,
)
from portfolio_dashboard.quote_cache import choose_valuation
from portfolio_dashboard.treemap import format_percent
from portfolio_dashboard.valuations import FxRate

D = Decimal
AS_OF = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
POLICY = ComparisonPolicy(AS_OF, max_fx_age=DAY, max_price_age=DAY)

EUR = QuoteUnit("EUR", D(1))
USD = QuoteUnit("USD", D(1))
GBP = QuoteUnit("GBP", D(1))
PENCE = QuoteUnit("GBP", D("0.01"))
SHARE_ID = "synthetic-share"
PROVIDER_NAME = "synthetic-provider"


# ---------------------------------------------------------------- fixtures


def model(
    value="100",
    currency="EUR",
    scenario=Scenario.BASE,
    *,
    target=None,
    instrument_id=SHARE_ID,
    valuation_at=AT,
    blocked=False,
    limitations=("estimate_not_confidence_interval",),
):
    """A model result with a chosen per-share value (the DCF itself is tested apart)."""
    inputs = InputReference(instrument_id, "synthetic-class", REPORT, "synthetic-v1")
    step = CalculationStep(
        "value_per_share", "synthetic", D(value), f"{currency} per share"
    )
    issues = (
        (QualityIssue(QualityStatus.INCOMPLETE, "scenario_components"),)
        if blocked
        else (QualityIssue(QualityStatus.BOOK_VALUE_APPROXIMATION, "synthetic_note"),)
    )
    result = IntrinsicValueResult(
        inputs,
        "synthetic-assumptions",
        scenario,
        "synthetic-model",
        valuation_at,
        currency,
        issues,
        ("synthetic-reference",),
        (step,),
        None if blocked else D(value),
        None if blocked or target is None else D(target),
        None if blocked else D("0.5"),
    )
    return ScenarioCalculation(result, limitations)


def listing(unit=EUR, instrument_id=SHARE_ID, venue="SYN-A", form=EquityForm.SHARE):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=instrument_id,
            trading_venue=venue,
            quote_unit=unit,
            provider_mapping=ProviderMapping(PROVIDER_NAME, f"DEMO-{venue}"),
        ),
        equity_form=form,
    )


def provider_price(
    price,
    unit=EUR,
    *,
    observed=AS_OF - HOUR,
    retrieved=AS_OF - timedelta(minutes=30),
    source=ValuationSource.PROVIDER,
    warnings=(),
):
    return Valuation(
        D(price),
        ValueStatus.AVAILABLE,
        Provenance(source, retrieved, observed, PROVIDER_NAME),
        unit,
        warnings,
    )


def fx(
    base,
    rate,
    quote="EUR",
    *,
    observed=AS_OF - timedelta(minutes=10),
    source=ValuationSource.PROVIDER,
    warnings=(),
):
    return FxRate(
        base,
        quote,
        D(rate),
        Provenance(source, AS_OF - timedelta(minutes=5), observed, "synthetic-fx"),
        warnings,
    )


def compare(calc, unit=EUR, price="80", rates=None, policy=POLICY, venue_unit=None):
    """Compare on a listing quoting in ``unit`` (provider price in that unit)."""
    verified = listing(venue_unit or unit)
    quote = None if price is None else provider_price(price, unit)
    return compare_to_price(
        calc, verified.instrument, quote, (verified,), rates, policy
    )


def fraction(value: Decimal) -> Fraction:
    return Fraction(value)


# ------------------------------------------------ the two key figures, 80 vs 100


def test_price_80_model_100_gives_20_percent_discount_and_25_percent_distance():
    comparison = compare(model("100"), price="80")

    assert comparison.comparable
    assert comparison.comparison_reasons == ()
    assert comparison.discount_to_model_percent == D(20)
    assert comparison.distance_to_model_percent == D(25)
    assert comparison.discount_to_model_percent != comparison.distance_to_model_percent
    assert comparison.position is PricePosition.BELOW_MODEL_VALUE
    assert comparison.comparison_currency == "EUR"
    assert (comparison.compared_model_value, comparison.compared_price) == (100, 80)
    assert comparison.fx_checks == ()
    assert comparison.notes == ()
    assert format_percent(comparison.discount_to_model_percent) == "+20,00 %"
    assert format_percent(comparison.distance_to_model_percent) == "+25,00 %"


def test_price_above_model_gives_negative_figures_with_explicit_position():
    comparison = compare(model("100"), price="125")

    # Discount: 1 - 125/100 = -25 %; distance: 100/125 - 1 = -20 %.
    assert comparison.discount_to_model_percent == D(-25)
    assert comparison.distance_to_model_percent == D(-20)
    assert comparison.position is PricePosition.ABOVE_MODEL_VALUE
    assert format_percent(comparison.discount_to_model_percent) == "-25,00 %"


def test_price_equal_to_model_is_zero_not_a_discount():
    comparison = compare(model("100"), price="100")

    assert comparison.discount_to_model_percent == 0
    assert comparison.distance_to_model_percent == 0
    assert comparison.position is PricePosition.AT_MODEL_VALUE


def test_rounding_happens_only_in_the_display():
    comparison = compare(model("3"), price="2")

    # 1 - 2/3 = 1/3 -> 33,333... %; 3/2 - 1 = 50 %.
    exact = Fraction(100, 3)
    assert abs(fraction(comparison.discount_to_model_percent) - exact) < Fraction(1, 10**40)
    assert comparison.discount_to_model_percent != D("33.33")
    assert comparison.distance_to_model_percent == D(50)
    assert format_percent(comparison.discount_to_model_percent) == "+33,33 %"
    assert comparison.model_value == D(3)
    assert comparison.price_major == D(2)


def test_subcent_prices_keep_all_digits_until_display():
    comparison = compare(model("12.3456789"), price="12.3456781")

    expected = (1 - Fraction("12.3456781") / Fraction("12.3456789")) * 100
    assert abs(fraction(comparison.discount_to_model_percent) - expected) < Fraction(
        1, 10**40
    )
    assert comparison.compared_price == D("12.3456781")


# ------------------------------------------------------ currencies, units, FX


def test_pence_price_is_scaled_to_pounds_before_comparison():
    calc = model("2", currency="GBP")
    comparison = compare(calc, unit=PENCE, price="150")

    # 150 pence = 1,50 GBP: discount 1 - 1,5/2 = 25 %, distance 2/1,5 - 1 = 33,33 %.
    assert comparison.price_currency == "GBP"
    assert comparison.price_major == D("1.5")
    assert comparison.comparison_currency == "GBP"
    assert comparison.discount_to_model_percent == D(25)
    assert abs(
        fraction(comparison.distance_to_model_percent) - Fraction(100, 3)
    ) < Fraction(1, 10**40)
    # No FX took part in the ratio; the missing EUR path only affects the display.
    assert comparison.eur.reasons == (ComparisonReason.FX_MISSING,)


def test_unscaled_pence_would_be_wrong_and_is_not_what_happens():
    calc = model("2", currency="GBP")
    comparison = compare(calc, unit=PENCE, price="150")

    # 150 as pounds would lie far above the model value of 2 GBP.
    assert comparison.position is PricePosition.BELOW_MODEL_VALUE
    assert comparison.compared_price == D("1.5")


def test_eur_amounts_use_the_directed_rate_for_each_side():
    calc = model("2", currency="GBP", target="1.6")
    rates = {"GBP": fx("GBP", "1.2")}
    comparison = compare(calc, unit=PENCE, price="150", rates=rates)

    # EUR per GBP 1,2: model 2 -> 2,4; target 1,6 -> 1,92; price 1,5 GBP -> 1,8.
    assert comparison.eur.model_value == D("2.4")
    assert comparison.eur.target_price == D("1.92")
    assert comparison.eur.price == D("1.8")
    assert comparison.eur.reasons == ()
    # The ratio is unchanged by the shared rate and stays a same-currency figure.
    assert comparison.discount_to_model_percent == D(25)
    assert comparison.comparison_currency == "GBP"
    (check,) = comparison.fx_checks
    assert check.usable
    assert check.rate is rates["GBP"]
    assert check.age == timedelta(minutes=10)


def test_same_currency_comparison_survives_missing_fx_and_keeps_model_visible():
    comparison = compare(model("100", currency="USD"), unit=USD, price="80")

    assert comparison.comparable
    assert comparison.discount_to_model_percent == D(20)
    assert comparison.comparison_currency == "USD"
    assert comparison.model_value == D(100)
    assert comparison.reporting_currency == "USD"
    assert comparison.eur.model_value is None
    assert comparison.eur.price is None
    assert comparison.eur.reasons == (ComparisonReason.FX_MISSING,)


def test_different_currencies_are_compared_in_eur_with_both_conversions():
    calc = model("100", currency="USD")
    rates = {"USD": fx("USD", "0.8")}
    comparison = compare(calc, unit=EUR, price="70", rates=rates)

    # USD model 100 -> 80 EUR; EUR price 70 is not converted again.
    assert comparison.comparable
    assert comparison.comparison_currency == "EUR"
    assert comparison.compared_model_value == D(80)
    assert comparison.compared_price == D(70)
    assert comparison.discount_to_model_percent == D("12.5")
    assert abs(
        fraction(comparison.distance_to_model_percent) - Fraction(100, 7)
    ) < Fraction(1, 10**40)
    assert ComparisonNote.COMPARED_VIA_EUR in comparison.notes
    assert comparison.reporting_currency == "USD"
    assert comparison.model_value == D(100)  # the reporting-currency value stays


def test_two_foreign_currencies_each_use_their_own_rate():
    calc = model("100", currency="USD")
    rates = {"USD": fx("USD", "0.8"), "GBP": fx("GBP", "1.2")}
    comparison = compare(calc, unit=PENCE, price="5000", rates=rates)

    # Model 100 USD = 80 EUR; price 5000 pence = 50 GBP = 60 EUR.
    assert comparison.compared_model_value == D(80)
    assert comparison.compared_price == D(60)
    assert comparison.discount_to_model_percent == D(25)
    assert abs(
        fraction(comparison.distance_to_model_percent) - Fraction(100, 3)
    ) < Fraction(1, 10**40)
    assert {c.currency for c in comparison.fx_checks} == {"USD", "GBP"}


def test_inverse_fx_direction_is_refused_not_inverted():
    calc = model("100", currency="USD")
    inverse = fx("EUR", "1.25", quote="USD")  # 1,25 USD per EUR, not USD -> EUR
    comparison = compare(calc, unit=EUR, price="70", rates={"USD": inverse})

    assert not comparison.comparable
    assert comparison.comparison_reasons == (ComparisonReason.FX_DIRECTION_MISMATCH,)
    assert comparison.discount_to_model_percent is None
    assert comparison.distance_to_model_percent is None
    assert comparison.compared_model_value is None
    assert comparison.eur.model_value is None
    (check,) = comparison.fx_checks
    assert check.rate is inverse and not check.usable
    assert comparison.model_value == D(100)
    assert comparison.price_major == D(70)


def test_correct_direction_gives_the_value_the_inverse_misreading_would_not():
    calc = model("100", currency="USD")
    right = compare(calc, price="70", rates={"USD": fx("USD", "0.8")})
    # Misreading 1,25 USD/EUR as EUR per USD would give 125 EUR and a "premium".
    assert right.compared_model_value == D(80)
    assert right.position is PricePosition.BELOW_MODEL_VALUE


def test_wrong_target_currency_is_a_direction_mismatch():
    calc = model("100", currency="USD")
    comparison = compare(
        calc, price="70", rates={"USD": fx("USD", "0.9", quote="GBP")}
    )

    assert comparison.comparison_reasons == (ComparisonReason.FX_DIRECTION_MISMATCH,)


def test_missing_fx_blocks_different_currencies_without_hiding_model_or_price():
    comparison = compare(model("100", currency="USD"), unit=EUR, price="70")

    assert not comparison.comparable
    assert comparison.comparison_reasons == (ComparisonReason.FX_MISSING,)
    assert comparison.model_value == D(100)
    assert comparison.reporting_currency == "USD"
    assert comparison.price_major == D(70)
    assert comparison.eur.price == D(70)  # the EUR price needs no FX
    assert comparison.eur.model_value is None
    assert comparison.discount_to_model_percent is None


def test_stale_fx_blocks_the_conversion_and_exactly_at_the_limit_is_allowed():
    calc = model("100", currency="USD")
    stale = fx("USD", "0.8", observed=AS_OF - DAY - timedelta(seconds=1))
    at_limit = fx("USD", "0.8", observed=AS_OF - DAY)

    blocked = compare(calc, price="70", rates={"USD": stale})
    allowed = compare(calc, price="70", rates={"USD": at_limit})

    assert blocked.comparison_reasons == (ComparisonReason.FX_STALE,)
    assert blocked.fx_checks[0].age == DAY + timedelta(seconds=1)
    assert blocked.eur.model_value is None
    assert allowed.comparable
    assert allowed.fx_checks[0].age == DAY


def test_fx_with_unknown_or_future_time_blocks():
    calc = model("100", currency="USD")
    unknown = compare(calc, price="70", rates={"USD": fx("USD", "0.8", observed=None)})
    future = compare(
        calc,
        price="70",
        rates={"USD": fx("USD", "0.8", observed=AS_OF + timedelta(seconds=1))},
    )

    assert unknown.comparison_reasons == (ComparisonReason.FX_TIME_UNKNOWN,)
    assert future.comparison_reasons == (ComparisonReason.FX_TIME_AFTER_AS_OF,)


def test_stale_fx_does_not_block_a_same_currency_comparison():
    calc = model("100", currency="USD")
    stale = fx("USD", "0.8", observed=AS_OF - 3 * DAY)
    comparison = compare(calc, unit=USD, price="80", rates={"USD": stale})

    assert comparison.comparable
    assert comparison.discount_to_model_percent == D(20)
    assert comparison.eur.reasons == (ComparisonReason.FX_STALE,)
    assert comparison.eur.model_value is None


def test_cached_fx_is_usable_within_the_limit_and_visibly_marked():
    calc = model("100", currency="USD")
    cached = fx(
        "USD",
        "0.8",
        observed=AS_OF - 2 * HOUR,
        source=ValuationSource.CACHED_PROVIDER,
        warnings=(DataWarning("cached_provider_value", "synthetic"),),
    )
    comparison = compare(calc, price="70", rates={"USD": cached})

    assert comparison.comparable
    assert ComparisonNote.FX_CACHED in comparison.notes
    assert [w.code for w in comparison.warnings] == ["cached_provider_value"]


def test_sheet_eur_replacement_price_is_not_converted_a_second_time():
    calc = model("100", currency="USD")
    verified = listing(USD)
    missing = Valuation(
        None,
        ValueStatus.MISSING,
        Provenance(ValuationSource.PROVIDER, AS_OF, provider=PROVIDER_NAME),
        warnings=(DataWarning("provider_failure", "synthetic"),),
    )
    sheet = Valuation(
        D(60),
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.SHEET, AS_OF - HOUR),
        EUR,
    )
    chosen = choose_valuation(missing, sheet)
    rates = {"USD": fx("USD", "0.8")}

    comparison = compare_to_price(
        calc, verified.instrument, chosen, (verified,), rates, POLICY
    )

    # Model 100 USD -> 80 EUR, replacement price 60 EUR (48 would be a double FX).
    assert comparison.comparable
    assert comparison.price_currency == "EUR"
    assert comparison.compared_price == D(60)
    assert comparison.eur.price == D(60)
    assert comparison.discount_to_model_percent == D(25)
    assert abs(
        fraction(comparison.distance_to_model_percent) - Fraction(100, 3)
    ) < Fraction(1, 10**40)
    assert {c.currency for c in comparison.fx_checks} == {"USD"}
    assert ComparisonNote.PRICE_SHEET_REPLACEMENT in comparison.notes
    assert ComparisonNote.PRICE_TIME_UNKNOWN in comparison.notes
    assert comparison.snapshot.price_source is ValuationSource.SHEET
    assert comparison.snapshot.price_observed_at is None
    assert comparison.snapshot.price_age is None
    codes = {w.code for w in comparison.warnings}
    assert {"price_source_fallback", "sheet_value_quote_time_unknown"} <= codes


def test_sheet_eur_replacement_without_fx_keeps_the_eur_price_and_blocks_the_ratio():
    calc = model("100", currency="USD")
    verified = listing(USD)
    sheet = Valuation(
        D(60),
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.SHEET, AS_OF - HOUR, AS_OF - HOUR),
        EUR,
    )
    comparison = compare_to_price(
        calc, verified.instrument, sheet, (verified,), None, POLICY
    )

    assert comparison.comparison_reasons == (ComparisonReason.FX_MISSING,)
    assert comparison.eur.price == D(60)
    assert comparison.model_value == D(100)
    assert comparison.discount_to_model_percent is None


# --------------------------------------------------------------------- identity


def test_second_listing_of_the_same_security_in_another_currency_is_comparable():
    calc = model("100", currency="USD")
    usd_listing = listing(USD, venue="SYN-US")
    eur_listing = listing(EUR, venue="SYN-DE")
    registry = (usd_listing, eur_listing)
    rates = {"USD": fx("USD", "0.8")}

    on_usd = compare_to_price(
        calc, usd_listing.instrument, provider_price("80", USD), registry, rates, POLICY
    )
    on_eur = compare_to_price(
        calc, eur_listing.instrument, provider_price("64", EUR), registry, rates, POLICY
    )

    # Same share: 80 USD against 100 USD, and 64 EUR against 100 USD = 80 EUR.
    assert on_usd.discount_to_model_percent == D(20)
    assert on_eur.discount_to_model_percent == D(20)


def test_price_of_another_security_is_never_compared():
    calc = model("100")
    other = listing(EUR, instrument_id="synthetic-other")
    comparison = compare_to_price(
        calc, other.instrument, provider_price("80"), (other,), None, POLICY
    )

    assert comparison.comparison_reasons == (ComparisonReason.INSTRUMENT_MISMATCH,)
    assert comparison.price is None and comparison.price_major is None
    assert comparison.eur.price is None
    assert comparison.discount_to_model_percent is None


def test_unverified_instrument_is_not_compared():
    calc = model("100")
    stranger = Instrument(
        InstrumentType.STOCK, instrument_id=SHARE_ID, trading_venue="SYN-X"
    )
    comparison = compare_to_price(
        calc, stranger, provider_price("80"), (listing(),), None, POLICY
    )

    assert comparison.comparison_reasons == (ComparisonReason.INSTRUMENT_UNVERIFIED,)
    assert comparison.price is None


def test_adr_price_is_never_compared_with_a_share_valuation():
    calc = model("100")
    # An ADR with its own identity, and an ADR wrongly carrying the share's ID.
    adr_other = listing(USD, instrument_id="synthetic-adr", form=EquityForm.ADR)
    adr_same_id = listing(USD, form=EquityForm.ADR)
    for adr, expected in (
        (adr_other, ComparisonReason.INSTRUMENT_MISMATCH),
        (adr_same_id, ComparisonReason.ADR_RATIO_UNSUPPORTED),
    ):
        comparison = compare_to_price(
            calc, adr.instrument, provider_price("80", USD), (adr,), None, POLICY
        )
        assert comparison.comparison_reasons == (expected,)
        assert comparison.discount_to_model_percent is None
        assert comparison.price_major is None


def test_etf_is_not_a_common_share():
    calc = model("100")
    etf = VerifiedListing(
        Instrument(
            InstrumentType.ETF,
            instrument_id=SHARE_ID,
            trading_venue="SYN-A",
            quote_unit=EUR,
            provider_mapping=ProviderMapping(PROVIDER_NAME, "DEMO-ETF"),
        ),
        share_class="synthetic-class",
        isin="XX0000000000",
    )
    comparison = compare_to_price(
        calc, etf.instrument, provider_price("80"), (etf,), None, POLICY
    )

    assert comparison.comparison_reasons == (ComparisonReason.NOT_COMMON_SHARE,)


# ------------------------------------------------------ invalid and missing data


@pytest.mark.parametrize("price", [None, "missing", "invalid"])
def test_missing_or_invalid_price_blocks_but_keeps_the_model_value(price):
    verified = listing()
    if price in ("missing", "invalid"):
        status = ValueStatus.MISSING if price == "missing" else ValueStatus.INVALID
        quote = Valuation(
            None,
            status,
            Provenance(ValuationSource.PROVIDER, AS_OF, provider=PROVIDER_NAME),
            EUR,
        )
    else:
        quote = None
    expected = (
        ComparisonReason.PRICE_INVALID
        if price == "invalid"
        else ComparisonReason.PRICE_MISSING
    )
    comparison = compare_to_price(
        model("100"), verified.instrument, quote, (verified,), None, POLICY
    )

    assert comparison.comparison_reasons == (expected,)
    assert comparison.model_value == D(100)
    assert comparison.eur.model_value == D(100)
    assert comparison.discount_to_model_percent is None
    assert comparison.distance_to_model_percent is None
    assert comparison.position is None


def test_zero_price_is_not_divided_and_not_an_attractive_valuation():
    comparison = compare(model("100"), price="0")

    assert comparison.comparison_reasons == (ComparisonReason.PRICE_NOT_POSITIVE,)
    assert comparison.discount_to_model_percent is None
    assert comparison.distance_to_model_percent is None
    assert comparison.position is None
    assert comparison.price_major is None


def test_unknown_unit_and_unit_conflicts_block():
    verified = listing(EUR)
    unknown = Valuation(
        D(80),
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.PROVIDER, AS_OF, AS_OF, PROVIDER_NAME),
        QuoteUnit("EUR", None),
    )
    no_unit = replace(unknown, unit=None)
    wrong_unit = provider_price("80", USD)
    foreign_provider = Valuation(
        D(80),
        ValueStatus.AVAILABLE,
        Provenance(ValuationSource.PROVIDER, AS_OF, AS_OF, "other-provider"),
        EUR,
    )
    expectations = (
        (unknown, ComparisonReason.PRICE_UNIT_UNKNOWN),
        (no_unit, ComparisonReason.PRICE_UNIT_UNKNOWN),
        (wrong_unit, ComparisonReason.PRICE_UNIT_MISMATCH),
        (foreign_provider, ComparisonReason.PRICE_PROVIDER_UNVERIFIED),
    )
    for quote, expected in expectations:
        comparison = compare_to_price(
            model("100"), verified.instrument, quote, (verified,), None, POLICY
        )
        assert comparison.comparison_reasons == (expected,)
        assert comparison.discount_to_model_percent is None


def test_blocked_model_gives_no_figures_but_shows_the_price():
    calc = model("100", blocked=True)
    comparison = compare(calc, price="80")

    assert comparison.comparison_reasons == (ComparisonReason.MODEL_VALUE_UNAVAILABLE,)
    assert comparison.model_value is None
    assert comparison.eur.model_value is None
    assert comparison.price_major == D(80)
    assert comparison.eur.price == D(80)
    assert comparison.discount_to_model_percent is None
    assert comparison.model_issues == calc.result.issues


def test_nonpositive_equity_value_from_the_real_calculator_is_never_compared():
    report = base_report(bridge=(100, 20, 4000, 10, 5))
    calc = calculate_scenario(report, doc_plan(report, Scenario.BASE))
    assert calc.result.value_per_share is None
    comparison = compare(calc, price="1")

    assert comparison.comparison_reasons == (ComparisonReason.MODEL_VALUE_UNAVAILABLE,)
    assert comparison.discount_to_model_percent is None
    assert comparison.distance_to_model_percent is None
    codes = {i.code for i in comparison.model_issues}
    assert "no_positive_common_equity_value" in codes


def test_several_reasons_stay_visible_together():
    calc = model("100", blocked=True)
    comparison = compare(calc, price="0")

    assert comparison.comparison_reasons == (
        ComparisonReason.MODEL_VALUE_UNAVAILABLE,
        ComparisonReason.PRICE_NOT_POSITIVE,
    )


# --------------------------------------------------------- times and snapshot


def test_old_cached_and_time_unknown_prices_remain_visible_not_hidden():
    calc = model("100")
    verified = listing()
    cached = provider_price(
        "80",
        observed=AS_OF - 3 * DAY,
        source=ValuationSource.CACHED_PROVIDER,
        warnings=(DataWarning("cached_provider_value", "synthetic"),),
    )
    comparison = compare_to_price(
        calc, verified.instrument, cached, (verified,), None, POLICY
    )

    assert comparison.comparable  # visible, not silently blocked
    assert comparison.price is cached
    assert set(comparison.notes) == {
        ComparisonNote.PRICE_CACHED,
        ComparisonNote.PRICE_OLDER_THAN_LIMIT,
    }
    assert comparison.snapshot.price_age == 3 * DAY
    assert comparison.snapshot.price_observed_at == AS_OF - 3 * DAY
    assert comparison.snapshot.price_retrieved_at == AS_OF - timedelta(minutes=30)
    assert comparison.snapshot.price_source is ValuationSource.CACHED_PROVIDER
    assert comparison.snapshot.as_of == AS_OF
    assert comparison.snapshot.model_valuation_at == AT
    assert [w.code for w in comparison.warnings] == ["cached_provider_value"]


def test_future_dated_price_or_model_blocks_the_snapshot():
    future_price = compare(model("100"), price="80")
    verified = listing()
    quote = provider_price("80", observed=AS_OF + timedelta(seconds=1))
    blocked_price = compare_to_price(
        model("100"), verified.instrument, quote, (verified,), None, POLICY
    )
    blocked_model = compare(
        model("100", valuation_at=AS_OF + timedelta(days=1)), price="80"
    )

    assert future_price.comparable
    assert blocked_price.comparison_reasons == (ComparisonReason.PRICE_TIME_AFTER_AS_OF,)
    assert blocked_model.comparison_reasons == (
        ComparisonReason.MODEL_VALUATION_AFTER_AS_OF,
    )
    assert blocked_model.model_value == D(100)


def test_model_age_and_per_share_scope_are_stated_as_limitations():
    comparison = compare(model("100"), price="80")

    assert "model_value_as_of_valuation_date_not_rolled_forward" in comparison.limitations
    assert "per_share_only_no_quantity_or_cost_basis" in comparison.limitations
    assert comparison.snapshot.model_valuation_at == AT


def test_policy_requires_explicit_aware_time_and_positive_limits():
    with pytest.raises(ValueError):
        ComparisonPolicy(datetime(2026, 1, 1), DAY, DAY)
    with pytest.raises(ValueError):
        ComparisonPolicy(AS_OF, timedelta(0), DAY)
    with pytest.raises(ValueError):
        ComparisonPolicy(AS_OF, DAY, -DAY)
    with pytest.raises(TypeError):
        compare_to_price(model(), listing().instrument, None, (listing(),), None, None)


# ----------------------------------------- real calculator, hand-derived numbers


def test_real_scenarios_are_compared_in_the_given_order_with_one_snapshot():
    report = base_report()
    plans = doc_plans(report)
    ordered = (plans[2], plans[0], plans[1])  # optimistic, cautious, base
    valuation_set = calculate_scenarios(report, ordered)
    verified = listing()
    quote = provider_price("100")

    result = compare_scenarios_to_price(
        valuation_set, verified.instrument, quote, (verified,), None, POLICY
    )

    assert [c.scenario for c in result.comparisons] == [
        Scenario.OPTIMISTIC,
        Scenario.CAUTIOUS,
        Scenario.BASE,
    ]
    assert result.missing == ()
    for comparison in result.comparisons:
        value = Fraction(DOC_PER_SHARE[comparison.scenario])
        assert comparison.comparable
        assert abs(
            fraction(comparison.discount_to_model_percent)
            - (1 - Fraction(100) / value) * 100
        ) < Fraction(1, 10**11)
        assert abs(
            fraction(comparison.distance_to_model_percent)
            - (value / 100 - 1) * 100
        ) < Fraction(1, 10**11)
        assert comparison.price is quote
        assert comparison.snapshot.as_of == AS_OF
    assert result.get(Scenario.BASE).position is PricePosition.BELOW_MODEL_VALUE
    # Cautious value 99,48 lies below the price 100: a premium, i.e. negative.
    cautious = result.get(Scenario.CAUTIOUS)
    assert cautious.position is PricePosition.ABOVE_MODEL_VALUE
    assert cautious.discount_to_model_percent < 0
    assert cautious.distance_to_model_percent < 0


def test_model_limitations_and_trail_pass_through_unchanged():
    report = base_report()
    calc = calculate_scenario(report, doc_plan(report, Scenario.OPTIMISTIC))
    assert LIMIT_TERMINAL_DOMINANT in calc.limitations
    comparison = compare(calc, price="150")

    assert comparison.calculation is calc
    assert LIMIT_TERMINAL_DOMINANT in comparison.limitations
    assert comparison.limitations[: len(calc.limitations)] == calc.limitations
    assert comparison.calculation.result.steps == calc.result.steps
    assert comparison.model_issues == calc.result.issues
    assert comparison.model_value == calc.result.value_per_share


def test_target_price_is_converted_but_never_replaces_the_model_value():
    report = base_report()
    calc = calculate_scenario(
        report, doc_plan(report, Scenario.BASE, margin=D("0.2"))
    )
    comparison = compare(calc, price="100")

    # Target 127,650095311423 * 0,8 = 102,120076249138 (docs/INTRINSIC_VALUE.md).
    assert abs(comparison.target_price - D("102.120076249138")) < D("1e-12")
    assert comparison.eur.target_price == comparison.target_price
    assert comparison.compared_model_value == calc.result.value_per_share


def test_blocked_scenario_does_not_block_the_others():
    report = base_report()
    plans = doc_plans(report)
    broken = replace(plans[0], confirmed_at=None, confirmation_reference=None)
    valuation_set = calculate_scenarios(report, (broken, plans[1], plans[2]))
    verified = listing()

    result = compare_scenarios_to_price(
        valuation_set, verified.instrument, provider_price("100"), (verified,), None, POLICY
    )

    assert not result.get(Scenario.CAUTIOUS).comparable
    assert result.get(Scenario.CAUTIOUS).comparison_reasons == (
        ComparisonReason.MODEL_VALUE_UNAVAILABLE,
    )
    assert result.get(Scenario.BASE).comparable
    assert result.get(Scenario.OPTIMISTIC).comparable
    assert result.limitations == valuation_set.limitations


def test_partial_scenario_set_reports_missing_labels():
    report = base_report()
    valuation_set = calculate_scenarios(report, (doc_plan(report, Scenario.BASE),))
    verified = listing()

    result = compare_scenarios_to_price(
        valuation_set, verified.instrument, provider_price("100"), (verified,), None, POLICY
    )

    assert result.missing == (Scenario.CAUTIOUS, Scenario.OPTIMISTIC)
    assert result.get(Scenario.CAUTIOUS) is None


# ------------------------------------------------------------------- scope


def test_no_holdings_or_cost_basis_enter_the_comparison():
    names = set(inspect.signature(compare_to_price).parameters)
    assert names == {"calculation", "instrument", "price", "listings", "fx_rates", "policy"}
    fields = {f for f in price_comparison.PriceComparison.__slots__}
    assert not {f for f in fields if re.search(r"quantity|cost|holding|portfolio", f)}


def test_results_are_deterministic_and_inputs_unchanged():
    calc = model("100", currency="USD")
    rates = {"USD": fx("USD", "0.8")}
    verified = listing(EUR)
    quote = provider_price("70")
    first = compare_to_price(calc, verified.instrument, quote, (verified,), rates, POLICY)
    second = compare_to_price(calc, verified.instrument, quote, (verified,), rates, POLICY)

    assert first == second
    assert quote.price == D(70) and rates["USD"].rate == D("0.8")


def test_module_has_no_clock_network_provider_or_ui_dependency():
    source = Path(price_comparison.__file__).read_text(encoding="utf-8")
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
import portfolio_dashboard.price_comparison
"""
    subprocess.run([sys.executable, "-c", code], check=True)
