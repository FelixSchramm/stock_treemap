"""Versioned JSON only; fixed constructors and exact shapes for private records.

No Python class names, pickle, imports, eval or executable values in the format.
Numbers use Decimal strings; decoding re-runs all domain validation.
"""

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
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
from portfolio_dashboard.intrinsic_models import (
    ForecastYear,
    InputReference,
    Scenario,
    ScenarioAssumptions,
    TerminalPlan,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
    ValueStatus,
)
from portfolio_dashboard.private_store import PrivateStorageError
from portfolio_dashboard.scenarios import (
    ManualPriority,
    ManualPurpose,
    ManualValue,
    SavedScenario,
    ScenarioDocument,
)

CODEC_VERSION = 1


def _encode(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("finite stored numbers required")
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, VerifiedFundamentalIdentity):
        return {
            f.name: _listing_data(value.listing)
            if f.name == "listing"
            else _encode(getattr(value, f.name))
            for f in fields(value)
        }
    if is_dataclass(value):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError("unsupported stored value")


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError("invalid stored structure")
    return value


def _listing_data(listing):
    """Stored shape of a verified listing; unchanged since the first codec version."""
    if listing is None:
        return None
    instrument = listing.instrument
    unit, mapping = instrument.quote_unit, instrument.provider_mapping
    return {
        "instrument": {
            "instrument_type": instrument.instrument_type.value,
            "instrument_id": instrument.instrument_id,
            "display_ticker": instrument.display_ticker,
            "source_symbol": instrument.source_symbol,
            "trading_venue": instrument.trading_venue,
            "source_isin": instrument.source_isin,
            "source_venue": instrument.source_venue,
            "quote_unit": None
            if unit is None
            else {
                "currency": unit.currency,
                "scale_to_currency": None
                if unit.scale_to_currency is None
                else str(unit.scale_to_currency),
            },
            "provider_mapping": None
            if mapping is None
            else {"provider": mapping.provider, "symbol": mapping.symbol},
        },
        "equity_form": None
        if listing.equity_form is None
        else listing.equity_form.value,
        "isin": listing.isin,
        "wkn": listing.wkn,
        "share_class": listing.share_class,
        "product_id": listing.product_id,
    }


def _listing_from_data(value):
    if value is None:
        return None
    listing = _object(value, "instrument equity_form isin wkn share_class product_id")
    instrument = _object(
        listing["instrument"],
        "instrument_type instrument_id display_ticker source_symbol trading_venue "
        "source_isin source_venue quote_unit provider_mapping",
    ).copy()
    unit = instrument["quote_unit"]
    if unit is not None:
        unit = _object(unit, "currency scale_to_currency")
        scale = unit["scale_to_currency"]
        if scale is not None and not isinstance(scale, str):
            raise ValueError("invalid stored scale")
        instrument["quote_unit"] = QuoteUnit(
            unit["currency"], None if scale is None else Decimal(scale)
        )
    mapping = instrument["provider_mapping"]
    if mapping is not None:
        instrument["provider_mapping"] = ProviderMapping(
            **_object(mapping, "provider symbol")
        )
    instrument["instrument_type"] = InstrumentType(instrument["instrument_type"])
    return VerifiedListing(
        instrument=Instrument(**instrument),
        equity_form=None
        if listing["equity_form"] is None
        else EquityForm(listing["equity_form"]),
        **{key: listing[key] for key in ("isin", "wkn", "share_class", "product_id")},
    )


def _array(value, decode):
    if type(value) is not list:
        raise ValueError("invalid stored sequence")
    return tuple(decode(item) for item in value)


def _optional(value, decode):
    return None if value is None else decode(value)


def _decimal(value):
    if type(value) is not str:
        raise ValueError("invalid stored number")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("finite stored numbers required")
    return number


def _date(value):
    if type(value) is not str:
        raise ValueError("invalid stored date")
    return date.fromisoformat(value)


def _time(value):
    if type(value) is not str:
        raise ValueError("invalid stored time")
    return datetime.fromisoformat(value)


def _report(value):
    data = _object(value, "issuer_id report_set_id revision consolidation_scope")
    return ReportIdentity(**data)


def _source(value):
    return FundamentalSource(**_object(value, "provider entity_id"))


def _flow(value):
    data = _object(value, "start end kind fiscal_label complete")
    return FlowPeriod(
        _date(data["start"]),
        _date(data["end"]),
        PeriodKind(data["kind"]),
        data["fiscal_label"],
        data["complete"],
    )


def _period(value):
    if type(value) is not dict:
        raise ValueError("invalid stored period")
    if "start" in value:
        return _flow(value)
    if "period" in value:
        data = _object(value, "period split_basis")
        return WeightedSharesPeriod(_flow(data["period"]), data["split_basis"])
    if "split_basis" in value:
        data = _object(value, "as_of split_basis")
        return CurrentSharesPeriod(_date(data["as_of"]), data["split_basis"])
    return InstantPeriod(_date(_object(value, "as_of")["as_of"]))


def _unit(value):
    data = _object(value, "kind scale currency")
    return FundamentalUnit(
        UnitKind(data["kind"]), _optional(data["scale"], _decimal), data["currency"]
    )


def _provenance(value):
    data = _object(
        value,
        "source kind field_reference raw_definition normalization retrieved_at reported_at published_at",
    )
    return FieldProvenance(
        _source(data["source"]),
        EvidenceKind(data["kind"]),
        data["field_reference"],
        data["raw_definition"],
        data["normalization"],
        _optional(data["retrieved_at"], _time),
        _optional(data["reported_at"], _time),
        _optional(data["published_at"], _time),
    )


def _observation(value):
    data = _object(value, "field value status unit period report provenance")
    return FundamentalObservation(
        FundamentalField(data["field"]),
        _optional(data["value"], _decimal),
        ValueStatus(data["status"]),
        _optional(data["unit"], _unit),
        _period(data["period"]),
        _report(data["report"]),
        _provenance(data["provenance"]),
    )


def _adjustment(value):
    data = _object(
        value,
        "original replacement reason changed_at confirmed_at confirmation_reference book_value_approximation",
    )
    return ManualAdjustment(
        _observation(data["original"]),
        _observation(data["replacement"]),
        data["reason"],
        _time(data["changed_at"]),
        _optional(data["confirmed_at"], _time),
        data["confirmation_reference"],
        data["book_value_approximation"],
    )


def _identity(value):
    data = _object(
        value,
        "listing share_class_id issuer_id source consolidation_scope issuer_kind evidence_reference verified_at",
    )
    return VerifiedFundamentalIdentity(
        _listing_from_data(data["listing"]),
        data["share_class_id"],
        data["issuer_id"],
        _source(data["source"]),
        data["consolidation_scope"],
        IssuerKind(data["issuer_kind"]),
        data["evidence_reference"],
        _time(data["verified_at"]),
    )


def _capital(value):
    data = _object(
        value,
        "issuer_id source as_of ordinary_class_count includes_unlisted evidence_reference verified_at",
    )
    return CapitalStructure(
        data["issuer_id"],
        _source(data["source"]),
        _date(data["as_of"]),
        data["ordinary_class_count"],
        data["includes_unlisted"],
        data["evidence_reference"],
        _optional(data["verified_at"], _time),
    )


def _issue(value):
    data = _object(value, "status code")
    return QualityIssue(QualityStatus(data["status"]), data["code"])


def _originals(value):
    data = _object(
        value,
        "identity report input_version currency observations capital_structure bridge_date shares_period adjustments qualifications",
    )
    return FundamentalReportSet(
        _identity(data["identity"]),
        _report(data["report"]),
        data["input_version"],
        data["currency"],
        _array(data["observations"], _observation),
        _capital(data["capital_structure"]),
        _date(data["bridge_date"]),
        _period(data["shares_period"]),
        _array(data["adjustments"], _adjustment),
        _array(data["qualifications"], _issue),
    )


def _inputs(value):
    data = _object(value, "instrument_id share_class_id report input_version")
    return InputReference(
        data["instrument_id"],
        data["share_class_id"],
        _report(data["report"]),
        data["input_version"],
    )


def _manual(value):
    data = _object(value, "inputs adjustment purpose priority")
    return ManualValue(
        _inputs(data["inputs"]),
        _adjustment(data["adjustment"]),
        ManualPurpose(data["purpose"]),
        ManualPriority(data["priority"]),
    )


def _year(value):
    data = _object(
        value,
        "year period ebit operating_tax_rate depreciation_amortization capex change_operating_nwc",
    )
    return ForecastYear(
        data["year"],
        _flow(data["period"]),
        *(
            _optional(data[key], _decimal)
            for key in (
                "ebit",
                "operating_tax_rate",
                "depreciation_amortization",
                "capex",
                "change_operating_nwc",
            )
        ),
    )


def _terminal(value):
    data = _object(
        value, "nopat depreciation_amortization capex change_operating_nwc rationale"
    )
    return TerminalPlan(
        *(
            _optional(data[key], _decimal)
            for key in (
                "nopat",
                "depreciation_amortization",
                "capex",
                "change_operating_nwc",
            )
        ),
        data["rationale"],
    )


def _assumptions(value):
    data = _object(
        value,
        "inputs version scenario valuation_at currency years terminal wacc terminal_growth sustainable_roic safety_margin rationale confirmed_at confirmation_reference",
    )
    return ScenarioAssumptions(
        _inputs(data["inputs"]),
        data["version"],
        Scenario(data["scenario"]),
        _time(data["valuation_at"]),
        data["currency"],
        _array(data["years"], _year),
        _optional(data["terminal"], _terminal),
        *(
            _optional(data[key], _decimal)
            for key in ("wacc", "terminal_growth", "sustainable_roic", "safety_margin")
        ),
        data["rationale"],
        _optional(data["confirmed_at"], _time),
        data["confirmation_reference"],
    )


def _saved(value):
    data = _object(value, "assumptions model_version")
    return SavedScenario(_assumptions(data["assumptions"]), data["model_version"])


def encode_document(document: ScenarioDocument) -> str:
    return json.dumps(
        {"version": CODEC_VERSION, "document": _encode(document)}, allow_nan=False
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate stored key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON constant")


def decode_document(payload: str) -> ScenarioDocument:
    try:
        data = _object(
            json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant),
            "version document",
        )
        if type(data["version"]) is not int or data["version"] != CODEC_VERSION:
            raise ValueError("unsupported codec version")
        record = _object(
            data["document"],
            "originals data_version model_version changed_at manual_values scenarios revision",
        )
        return ScenarioDocument(
            _originals(record["originals"]),
            record["data_version"],
            record["model_version"],
            _time(record["changed_at"]),
            _array(record["manual_values"], _manual),
            _array(record["scenarios"], _saved),
            record["revision"],
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        InvalidOperation,
        OverflowError,
        RecursionError,
    ):
        raise PrivateStorageError("data") from None
