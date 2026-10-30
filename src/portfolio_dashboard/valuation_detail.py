"""Pure building blocks of the detail view «Innerer Wert» (M8-11, #80).

Everything here is deterministic and offline: no Streamlit, no clock (the caller
passes ``now``), no provider, store or network. The module turns the owner's text
input into the validated contracts of ``fundamentals``, ``intrinsic_models`` and
``scenarios`` and prepares the display of the history, the calculation path and
the sensitivity. It never supplies a financial default: an empty field stays an
error, a missing value is never zero, and the numbers of the model come unchanged
from ``dcf``.

Units of the input (shown in every label): money in millions of the reporting
currency, share counts in millions of shares, rates in percent. They are converted
to the major units / fractions of the contracts on parsing.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from html import escape

from portfolio_dashboard.dcf import (
    MODEL_VERSION,
    PLAN_TOLERANCE,
    SensitivityGrid,
    calculate_scenario,
    calculate_sensitivity,
    centered_axis,
)
from portfolio_dashboard.fundamentals import (
    CurrentSharesPeriod,
    EvidenceKind,
    FieldProvenance,
    FlowPeriod,
    FundamentalField,
    FundamentalObservation,
    FundamentalReportSet,
    FundamentalUnit,
    InstantPeriod,
    ManualAdjustment,
    PeriodKind,
    QualityIssue,
    QualityStatus,
    UnitKind,
    WeightedSharesPeriod,
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
from portfolio_dashboard.models import ValueStatus
from portfolio_dashboard.scenarios import (
    ManualPriority,
    ManualPurpose,
    ManualValue,
    SavedScenario,
    ScenarioDocument,
)
from portfolio_dashboard.treemap import NOT_AVAILABLE, _german

UNIT = Decimal(1_000_000)
UNIT_NAME = "Mio."
MAX_INPUT = Decimal(10) ** 12
MAX_FRACTION_DIGITS = 12
MAX_TEXT = 500
YEARS = 5
APP_CONFIRMATION = "In der App bestätigt"

SCENARIO_LABELS = {
    Scenario.CAUTIOUS: "Vorsichtig",
    Scenario.BASE: "Basis",
    Scenario.OPTIMISTIC: "Optimistisch",
}
SCENARIO_ORDER = (Scenario.CAUTIOUS, Scenario.BASE, Scenario.OPTIMISTIC)

NUMBER_HELP = (
    "Keine gültige Zahl. Erlaubt sind Ziffern mit Dezimalkomma oder -punkt, ohne "
    "Tausendertrennzeichen und ohne Einheit, z. B. 12,5."
)
_NUMBER = re.compile(r"[+-]?[0-9]+(?:[.,][0-9]+)?")


def markdown_text(text: str) -> str:
    """Escape owner or sheet text for ``st.markdown`` (HTML and markdown syntax)."""
    escaped = escape(text)
    for char in "\\`*_[]$~#|":
        escaped = escaped.replace(char, "\\" + char)
    return escaped


class InputError(ValueError):
    """German explanation for one field; it never contains the entered text."""


# --- parsing and formatting --------------------------------------------------------


def parse_number(text: str | None) -> Decimal:
    """Finite decimal from German or English notation; exponents and units are errors."""
    raw = (text or "").strip()
    if not raw:
        raise InputError("Bitte einen Wert eingeben.")
    if not _NUMBER.fullmatch(raw):
        raise InputError(NUMBER_HELP)
    value = Decimal(raw.replace(",", "."))
    if abs(value) > MAX_INPUT:
        raise InputError("Der Wert ist zu groß.")
    if -value.as_tuple().exponent > MAX_FRACTION_DIGITS:
        raise InputError(f"Höchstens {MAX_FRACTION_DIGITS} Nachkommastellen.")
    return value


def parse_percent(text: str | None) -> Decimal:
    """Percent input (``12,5`` or ``12,5 %``) as a fraction (0.125)."""
    raw = (text or "").strip()
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    return parse_number(raw) / 100


def parse_money(text: str | None) -> Decimal:
    """Amount in millions as major units of the reporting currency."""
    return parse_number(text) * UNIT


def to_input(value: Decimal) -> str:
    """Plain German input text of a number (no exponent, no trailing zeros)."""
    return format(value.normalize(), "f").replace(".", ",")


def money_input(value: Decimal | None) -> str:
    return "" if value is None else to_input(value / UNIT)


def percent_input(value: Decimal | None) -> str:
    return "" if value is None else to_input(value * 100)


def format_money(value: Decimal | None, places: int = 2) -> str:
    """Major units shown in millions; a missing value is 'n. v.', never zero."""
    return NOT_AVAILABLE if value is None else _german(value / UNIT, places)


def format_percent(value: Decimal | None, places: int = 2) -> str:
    return NOT_AVAILABLE if value is None else f"{_german(value * 100, places)} %"


def format_per_share(value: Decimal | None, currency: str) -> str:
    return NOT_AVAILABLE if value is None else f"{_german(value, 2)} {currency}"


def format_date(value: date | datetime) -> str:
    return value.strftime("%d.%m.%Y")


def add_years(day: date, years: int) -> date:
    """Same month and day in another year; 29 February falls back to 28 February."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


# --- field catalog: labels, units and explanations --------------------------------

FIELD_NAMES = {
    FundamentalField.EBIT: "EBIT (operatives Ergebnis)",
    FundamentalField.OPERATING_TAX_RATE: "Operativer Steuersatz",
    FundamentalField.DEPRECIATION_AMORTIZATION: "Abschreibungen (D&A)",
    FundamentalField.CAPEX: "Investitionen (Capex)",
    FundamentalField.OPERATING_NWC: "Operatives Nettoumlaufvermögen (NWC)",
    FundamentalField.CHANGE_OPERATING_NWC: "Veränderung des operativen NWC (ΔNWC)",
    FundamentalField.PROVIDER_FCF: "Free Cashflow laut Anbieter (nur Kontrollwert)",
    FundamentalField.EXCESS_CASH: "Überschüssige liquide Mittel",
    FundamentalField.NONOPERATING_ASSETS: "Nichtoperative Vermögenswerte",
    FundamentalField.FINANCIAL_DEBT: "Finanzschulden",
    FundamentalField.PREFERRED_CLAIMS: "Vorzugsansprüche",
    FundamentalField.MINORITY_CLAIMS: "Minderheitsansprüche",
    FundamentalField.OUTSTANDING_SHARES: "Aktuell ausstehende Aktien",
    FundamentalField.INCREMENTAL_DILUTION: "Inkrementelle Verwässerung",
    FundamentalField.WEIGHTED_EPS_SHARES: "Gewichtete Aktienzahl (nur Kontrollwert)",
}
FIELD_HELP = {
    FundamentalField.EBIT: (
        "Operatives Ergebnis vor Zinsen und Steuern; Aktienvergütung bleibt Aufwand. "
        "Im Modell muss es in jedem Historienjahr positiv sein."
    ),
    FundamentalField.OPERATING_TAX_RATE: (
        "Erwartete Steuer auf das operative Ergebnis in Prozent (0 bis unter 100), "
        "ohne Steuervorteil der Finanzierung. Der Anbieter liefert nur die effektive "
        "Quote als Vorschlag."
    ),
    FundamentalField.DEPRECIATION_AMORTIZATION: (
        "Operative Abschreibungen, nicht negativ, ohne Aktienvergütung."
    ),
    FundamentalField.CAPEX: (
        "Auszahlungen für operative Investitionen als positiver Betrag; er wird abgezogen."
    ),
    FundamentalField.OPERATING_NWC: (
        "Nicht zahlungswirksame operative Umlaufaktiva minus nichtverzinsliche operative "
        "kurzfristige Verbindlichkeiten, ohne Liquidität und Finanzschulden; auch negativ möglich."
    ),
    FundamentalField.EXCESS_CASH: (
        "Frei verfügbare Liquidität abzüglich der betrieblich benötigten oder gebundenen Mittel; "
        "wird addiert. Bruttoliquidität ist nur eine Obergrenze."
    ),
    FundamentalField.NONOPERATING_ASSETS: (
        "Nicht im operativen Wert enthaltene Vermögenswerte; wird addiert. "
        "Nichts doppelt zählen (Liquidität, operatives Ergebnis)."
    ),
    FundamentalField.FINANCIAL_DEBT: (
        "Verzinsliche Ansprüche inklusive konsistent behandelter Leasingverbindlichkeiten; "
        "wird abgezogen. Nettoschuld nicht zusätzlich abziehen."
    ),
    FundamentalField.PREFERRED_CLAIMS: (
        "Vorrangige Ansprüche anderer Kapitalgeber; wird abgezogen. Ein belegter Wert 0 ist zulässig, "
        "eine fehlende Angabe ist nie 0."
    ),
    FundamentalField.MINORITY_CLAIMS: (
        "Ansprüche von Minderheitsgesellschaftern am konsolidierten Unternehmenswert; wird einmal "
        "abgezogen. Ein belegter Wert 0 ist zulässig, eine fehlende Angabe ist nie 0."
    ),
    FundamentalField.OUTSTANDING_SHARES: (
        "Aktuelle Stammaktien der einzigen Stammaktienklasse ohne eigene Aktien, splitbereinigt."
    ),
    FundamentalField.INCREMENTAL_DILUTION: (
        "Zusätzliche Aktien aus bestehenden Ansprüchen in dieselbe Klasse, nach nachvollziehbarer "
        "Methode; ein belegter Wert 0 ist zulässig."
    ),
}
# Model inputs the owner may supplement or normalize (controls and the derived ΔNWC
# are neither inputs nor editable).
EDITABLE_FIELDS = tuple(
    f
    for f in FundamentalField
    if f
    not in {
        FundamentalField.PROVIDER_FCF,
        FundamentalField.WEIGHTED_EPS_SHARES,
        FundamentalField.CHANGE_OPERATING_NWC,
    }
)
_ANY_SIGN = frozenset({FundamentalField.EBIT, FundamentalField.OPERATING_NWC})


def unit_kind(field: FundamentalField) -> UnitKind:
    if field is FundamentalField.OPERATING_TAX_RATE:
        return UnitKind.RATE
    if field in {
        FundamentalField.OUTSTANDING_SHARES,
        FundamentalField.INCREMENTAL_DILUTION,
        FundamentalField.WEIGHTED_EPS_SHARES,
    }:
        return UnitKind.SHARES
    return UnitKind.MONEY


def unit_hint(field: FundamentalField, currency: str) -> str:
    kind = unit_kind(field)
    if kind is UnitKind.RATE:
        return "Prozent"
    if kind is UnitKind.SHARES:
        return f"{UNIT_NAME} Stück"
    return f"{UNIT_NAME} {currency}"


def period_label(period) -> str:
    if isinstance(period, FlowPeriod):
        return f"Geschäftsjahr {format_date(period.start)}–{format_date(period.end)}"
    if isinstance(period, InstantPeriod):
        return f"Stichtag {format_date(period.as_of)}"
    if isinstance(period, CurrentSharesPeriod):
        return f"Stichtag {format_date(period.as_of)} (aktueller Kapitalstand)"
    if isinstance(period, WeightedSharesPeriod):
        return f"Geschäftsjahr bis {format_date(period.period.end)} (gewichtet)"
    return NOT_AVAILABLE


def absolute(observation: FundamentalObservation) -> Decimal | None:
    """Usable value in major units / pieces, else None (never zero)."""
    unit = observation.unit
    if (
        observation.status is not ValueStatus.AVAILABLE
        or observation.value is None
        or unit is None
        or not unit.known
    ):
        return None
    return observation.value * unit.scale


def format_observation(observation: FundamentalObservation, currency: str) -> str:
    value = absolute(observation)
    if value is None:
        return "fehlt" if observation.status is ValueStatus.MISSING else "ungültig"
    kind = unit_kind(observation.field)
    if kind is UnitKind.RATE:
        return format_percent(value)
    suffix = "Mio. Stück" if kind is UnitKind.SHARES else f"Mio. {currency}"
    return f"{format_money(value)} {suffix}"


# --- explanations of issue codes and model limits --------------------------------

ISSUE_TEXT = {
    "instrument_model": "Instrument oder Emittent liegt außerhalb des Modells (nur Stammaktien von Nichtfinanzunternehmen)",
    "issuer_kind_unknown": "Emittentenart unbekannt",
    "ordinary_class_count_unknown": "Anzahl der Stammaktienklassen des Emittenten unbekannt (private Zuordnung)",
    "multiple_classes": "Mehrere Stammaktienklassen: Modell nicht unterstützt",
    "unknown_unit": "Einheit oder Skalierung einer Angabe unbekannt",
    "future_evidence": "Beleg mit Zeitpunkt nach dem Bewertungsstichtag",
    "future_observation": "Beobachtung für einen Zeitraum nach dem Bewertungsstichtag",
    "future_identity_evidence": "Identitätsnachweis nach dem Bewertungsstichtag",
    "future_adjustment": "Manuelle Änderung nach dem Bewertungsstichtag",
    "minimum_history": "Weniger als drei vollständige Geschäftsjahre",
    "mixed_flow_periods": "Geschäftsjahre nicht lückenlos oder mit anderen Zeiträumen vermischt",
    "historical_components": "Eine Pflichtangabe der Historie fehlt (EBIT, Steuersatz, D&A, Capex oder NWC-Stichtage)",
    "capital_bridge": "Eine Pflichtangabe der Kapitalbrücke fehlt (Cash, nichtoperative Vermögenswerte, Schulden, Vorzugs- oder Minderheitsansprüche)",
    "current_shares": "Aktuelle Aktienzahl oder Verwässerung fehlt",
    "bridge_age": "Bilanzstichtag der Kapitalbrücke älter als 550 Tage",
    "annual_report_age": "Jüngster Jahresabschluss älter als 550 Tage",
    "share_count_age": "Aktienzahl älter als 120 Tage",
    "ebit_definition": "EBIT-Definition noch nicht bestätigt (private Konfiguration: confirm_ebit_definition)",
    "share_split_basis": "Splitbasis der Aktienzahl noch nicht bestätigt (private Konfiguration: confirm_share_split_basis)",
    "lease_treatment": "Leasing-Behandlung von EBIT und Schulden noch nicht bestätigt (private Konfiguration: confirm_lease_treatment)",
    "manual_confirmation": "Manuelle Ergänzung noch nicht bestätigt",
    "manual_review_required": "Manuelle Ergänzung gehört zu einem früheren Bericht und muss neu geprüft werden",
    "scenario_confirmation": "Annahmen noch nicht bestätigt",
    "scenario_components": "Annahmen unvollständig (fünf Prognosejahre, Folgejahr, WACC, Wachstum und ROIC sind Pflicht)",
    "assumptions_input_mismatch": "Annahmen gehören zu einer anderen Datenbasis oder einem anderen Bericht und müssen neu geprüft werden",
    "scenario_missing": "Für dieses Szenario sind keine Annahmen gespeichert",
    "scenario_model_review_required": "Modellversion geändert: Annahmen neu prüfen",
    "historical_ebit_not_positive": "EBIT in einem Historienjahr nicht positiv: Modell nicht geeignet",
    "historical_fcff_not_positive": "FCFF in einem Historienjahr nicht positiv: Modell nicht geeignet",
    "nwc_change_inconsistent": "ΔNWC passt nicht zu den NWC-Ständen",
    "terminal_nopat_mismatch": "NOPAT des Folgejahres passt nicht zu NOPAT Jahr 5 × (1 + g)",
    "terminal_reinvestment_mismatch": "Capex + ΔNWC − D&A des Folgejahres ergibt nicht NOPAT × g ÷ ROIC",
    "wacc_not_positive": "WACC muss größer als 0 sein",
    "growth_negative": "Wachstum g darf nicht negativ sein",
    "growth_not_below_wacc": "Wachstum g muss unter dem WACC liegen",
    "roic_not_positive": "ROIC muss größer als 0 sein",
    "growth_not_below_roic": "Wachstum g muss unter dem ROIC liegen",
    "shares_not_positive": "Verwässerte Aktienzahl nicht positiv",
    "nonpositive_sustainable_fcff": "Nachhaltiger FCFF des Folgejahres nicht positiv",
    "no_positive_operating_value": "Operativer Wert nicht positiv",
    "no_positive_common_equity_value": "Wert des Stammaktionär-Eigenkapitals nicht positiv: kein Wert je Aktie",
    "calculation_input_unusable": "Eine Eingabe der Rechnung ist nicht verwertbar (Kapitalbrücke oder Aktienzahl)",
    "calculation_overflow": "Zahlenbereich der Rechnung überschritten",
    "annual_history_unavailable": "Keine verwertbaren Geschäftsjahre vom Anbieter",
}
_STATUS_PREFIX = {
    QualityStatus.MISSING: "Angabe fehlt",
    QualityStatus.INVALID: "Angabe ungültig",
}
_FIELD_BY_CODE = {f.value: f for f in FundamentalField}

LIMITATION_TEXT = {
    "estimate_not_confidence_interval": (
        "Der Wert ist eine Modellschätzung aus den eigenen Annahmen, kein statistisches "
        "Konfidenzintervall und keine Prognose."
    ),
    "constant_wacc_and_sustainable_roic": (
        "WACC, nachhaltiger ROIC und Wachstum werden als konstant angenommen."
    ),
    "year_end_cash_flows_no_mid_year_shift": (
        "Cashflows fallen am Jahresende an (kein Halbjahreseffekt); t = 0 ist der Bewertungsstichtag."
    ),
    "terminal_value_dominant": (
        "Terminalwertdominanz: Der Restwert trägt 75 % oder mehr des operativen Werts; "
        "kleine Änderungen von WACC oder g verschieben das Ergebnis stark."
    ),
    "negative_forecast_fcff": "Mindestens ein Prognosejahr hat einen negativen FCFF.",
    "scenario_range_is_model_estimate_not_confidence_interval": (
        "Die Spanne der drei Szenarien ist eine Modellspanne, kein Konfidenzintervall und keine sichere Prognose."
    ),
    "scenario_values_not_ordered": (
        "Die Szenariowerte liegen nicht in der Reihenfolge vorsichtig ≤ Basis ≤ optimistisch; "
        "die Zuordnung bleibt unverändert."
    ),
}


def explain_issue(issue: QualityIssue) -> str:
    """German explanation with the technical code; unknown codes stay readable."""
    text = ISSUE_TEXT.get(issue.code)
    if text is None:
        field_ = _FIELD_BY_CODE.get(issue.code)
        prefix = _STATUS_PREFIX.get(issue.status)
        if field_ is not None and prefix is not None:
            text = f"{prefix}: {FIELD_NAMES[field_]}"
        elif issue.status is QualityStatus.MANUALLY_SUPPLEMENTED and field_:
            text = f"Manuell ergänzt: {FIELD_NAMES[field_]}"
        elif issue.status is QualityStatus.BOOK_VALUE_APPROXIMATION and field_:
            text = f"Buchwert als Näherung: {FIELD_NAMES[field_]}"
        elif issue.status is QualityStatus.UNKNOWN_PUBLICATION and field_:
            text = f"Veröffentlichungsdatum unbekannt: {FIELD_NAMES[field_]}"
        else:
            text = "Hinweis des Systems"
    return f"{text} ({issue.code})"


def blocking_texts(issues: Sequence[QualityIssue]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(explain_issue(i) for i in issues if i.blocking))


def advisory_texts(issues: Sequence[QualityIssue]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(explain_issue(i) for i in issues if not i.blocking))


# --- scenario form -> assumptions ---------------------------------------------------

MONEY_YEAR_FIELDS = ("ebit", "da", "capex", "dnwc")
YEAR_FIELD_LABELS = {
    "ebit": "EBIT",
    "tax": "Operativer Steuersatz",
    "da": "Abschreibungen (D&A)",
    "capex": "Investitionen (Capex)",
    "dnwc": "Veränderung operatives NWC (ΔNWC)",
}
YEAR_FIELD_HELP = {
    "ebit": "Operatives Ergebnis vor Zinsen und Steuern dieses Prognosejahres.",
    "tax": "Erwartete Steuer auf das operative Ergebnis, in Prozent von 0 bis unter 100.",
    "da": "Operative Abschreibungen, nicht negativ.",
    "capex": "Operative Investitionen als positiver Betrag; wird abgezogen.",
    "dnwc": "Zunahme des Umlaufvermögens bindet Geld (positiver Wert wird abgezogen); Freisetzung ist negativ.",
}
YEAR_FIELD_UNITS = {
    "ebit": "money",
    "tax": "percent",
    "da": "money",
    "capex": "money",
    "dnwc": "money",
}
SCALAR_LABELS = {
    "wacc": "WACC (Kapitalkostensatz)",
    "growth": "Nachhaltiges Wachstum g",
    "roic": "Nachhaltiger ROIC",
    "margin": "Persönlicher Sicherheitsabschlag (optional)",
    "t.da": "Folgejahr: Abschreibungen (D&A)",
    "t.capex": "Folgejahr: Investitionen (Capex)",
    "t.dnwc": "Folgejahr: Veränderung operatives NWC (ΔNWC)",
    "rationale": "Begründung der Annahmen",
    "t.rationale": "Begründung des nachhaltigen Folgejahres",
}
SCALAR_HELP = {
    "wacc": "Nominaler Kapitalkostensatz in Prozent, größer als 0; gilt konstant für alle Jahre. Keine Vorgabe: Es ist Ihre Annahme.",
    "growth": "Langfristiges Wachstum nach Jahr 5 in Prozent: mindestens 0 und unter WACC und ROIC.",
    "roic": "Nachhaltige Kapitalrendite des Folgejahres in Prozent, größer als 0 und größer als g. Bestimmt die nötige Reinvestition (NOPAT × g ÷ ROIC).",
    "margin": "Optional, 0 bis unter 100 Prozent. Der Zielpreis ist Wert je Aktie × (1 − Abschlag); der DCF-Wert bleibt unverändert. Leer = kein Zielpreis.",
    "t.da": "Abschreibungen des nachhaltigen Folgejahres (Jahr 6), nicht negativ.",
    "t.capex": "Investitionen des Folgejahres. Capex + ΔNWC − D&A muss die nötige Netto-Reinvestition NOPAT × g ÷ ROIC ergeben.",
    "t.dnwc": "ΔNWC des Folgejahres (Zunahme positiv).",
    "rationale": "Kurz: Worauf beruhen diese Annahmen? (Pflicht, wird mit dem Szenario gespeichert.)",
    "t.rationale": "Kurz: Warum ist das Folgejahr nachhaltig? (Pflicht.)",
}
TEXT_FIELDS = ("rationale", "t.rationale")
FIELD_IDS = (
    *(f"y{n}.{name}" for n in range(1, YEARS + 1) for name in YEAR_FIELD_LABELS),
    "wacc",
    "growth",
    "roic",
    "margin",
    "t.da",
    "t.capex",
    "t.dnwc",
    "rationale",
    "t.rationale",
)
FIRST_YEAR_ERROR_KEY = "first_year_end"
FORM_ERROR_KEY = "_form"
GENERIC_ERROR = (
    "Die Angaben konnten nicht in eine gültige Annahme übernommen werden. "
    "Bitte die markierten Felder prüfen."
)


def field_label(field_id: str) -> str:
    if field_id in SCALAR_LABELS:
        return SCALAR_LABELS[field_id]
    match = re.fullmatch(r"y([1-5])\.(\w+)", field_id)
    if match:
        return f"Jahr {match.group(1)}: {YEAR_FIELD_LABELS[match.group(2)]}"
    return field_id


def field_unit(field_id: str, currency: str) -> str:
    """Unit text shown in the label, e.g. ``Mio. EUR`` or ``%``."""
    if field_id in TEXT_FIELDS:
        return ""
    name = field_id.split(".")[-1]
    if field_id in {"wacc", "growth", "roic", "margin"} or name == "tax":
        return "%"
    return f"{UNIT_NAME} {currency}"


def field_help(field_id: str) -> str:
    if field_id in SCALAR_HELP:
        return SCALAR_HELP[field_id]
    return YEAR_FIELD_HELP[field_id.split(".")[-1]]


@dataclass(frozen=True, slots=True)
class ScenarioForm:
    """Text input of one scenario plus the first forecast year and the confirmation."""

    values: Mapping[str, str]
    first_year_end: date
    confirmed: bool = False

    def text(self, field_id: str) -> str:
        return self.values.get(field_id, "")


def default_first_year_end(originals: FundamentalReportSet, today: date) -> date:
    """End of the first fiscal year that starts on or after ``today``.

    Derived from the newest delivered annual period; a shown default, not an
    assumption (the owner can change it). Without an annual period: one year ahead.
    """
    ends = [
        o.period.end
        for o in originals.observations
        if o.field is FundamentalField.EBIT
        and isinstance(o.period, FlowPeriod)
        and o.period.kind is PeriodKind.FISCAL_YEAR
        and o.period.complete
    ]
    if not ends:
        return add_years(today, 1)
    last = max(ends)
    for k in range(1, 200):
        if add_years(last, k - 1) + timedelta(days=1) >= today:
            return add_years(last, k)
    return add_years(today, 1)


def form_from_saved(
    saved: SavedScenario | None, document: ScenarioDocument, today: date
) -> ScenarioForm:
    """Seed the editor. A confirmation is only carried over for the exact same data."""
    first_end = default_first_year_end(document.originals, today)
    if saved is None:
        return ScenarioForm({}, first_end, False)
    a = saved.assumptions
    values: dict[str, str] = {}
    for y in a.years:
        values[f"y{y.year}.ebit"] = money_input(y.ebit)
        values[f"y{y.year}.tax"] = percent_input(y.operating_tax_rate)
        values[f"y{y.year}.da"] = money_input(y.depreciation_amortization)
        values[f"y{y.year}.capex"] = money_input(y.capex)
        values[f"y{y.year}.dnwc"] = money_input(y.change_operating_nwc)
    values["wacc"] = percent_input(a.wacc)
    values["growth"] = percent_input(a.terminal_growth)
    values["roic"] = percent_input(a.sustainable_roic)
    values["margin"] = percent_input(a.safety_margin)
    if a.terminal is not None:
        values["t.da"] = money_input(a.terminal.depreciation_amortization)
        values["t.capex"] = money_input(a.terminal.capex)
        values["t.dnwc"] = money_input(a.terminal.change_operating_nwc)
        values["t.rationale"] = a.terminal.rationale
    values["rationale"] = a.rationale
    if a.years:
        first_end = a.years[0].period.end
    return ScenarioForm(
        {k: v for k, v in values.items() if v != ""},
        first_end,
        a.confirmed_at is not None and a.inputs == document.inputs,
    )


def year_period(first_year_end: date, number: int) -> FlowPeriod:
    """Forecast year ``number`` (1..5): twelve months, consecutive, ending anniversaries."""
    end = add_years(first_year_end, number - 1)
    start = add_years(first_year_end, number - 2) + timedelta(days=1)
    return FlowPeriod(
        start, end, PeriodKind.FISCAL_YEAR, f"FY ending {end.isoformat()}", True
    )


@dataclass(frozen=True, slots=True)
class TerminalHelp:
    """Derived terminal figures for the helper text (no stored value)."""

    nopat_5: Decimal
    nopat_6: Decimal
    net_reinvestment: Decimal
    required_capex: Decimal | None  # None until D&A and ΔNWC of the next year parse


@dataclass(frozen=True, slots=True)
class Built:
    """Either valid assumptions or the field errors; never partial values."""

    assumptions: ScenarioAssumptions | None
    errors: Mapping[str, str] = field(default_factory=dict)
    terminal: TerminalHelp | None = None


def _read(form: ScenarioForm, field_id: str, parser, errors: dict, **rules):
    text = form.text(field_id)
    try:
        value = parser(text)
    except InputError as exc:
        errors[field_id] = str(exc)
        return None
    low = rules.get("minimum")
    high = rules.get("below")
    message = rules.get("message")
    if low is not None and value < low:
        errors[field_id] = message
        return None
    if high is not None and value >= high:
        errors[field_id] = message
        return None
    if rules.get("positive") and value <= 0:
        errors[field_id] = message
        return None
    return value


def terminal_help(form: ScenarioForm) -> TerminalHelp | None:
    """NOPAT_5/6 and the required net reinvestment, if the inputs so far allow it."""
    try:
        ebit = parse_money(form.text("y5.ebit"))
        tax = parse_percent(form.text("y5.tax"))
        growth = parse_percent(form.text("growth"))
        roic = parse_percent(form.text("roic"))
    except InputError:
        return None
    if not 0 <= tax < 1 or growth < 0 or roic <= 0:
        return None
    nopat_5 = ebit * (1 - tax)
    nopat_6 = nopat_5 * (1 + growth)
    net = nopat_6 * growth / roic
    required = None
    try:
        da = parse_money(form.text("t.da"))
        dnwc = parse_money(form.text("t.dnwc"))
        required = net + da - dnwc
    except InputError:
        pass
    return TerminalHelp(nopat_5, nopat_6, net, required)


def build_assumptions(
    form: ScenarioForm,
    scenario: Scenario,
    document: ScenarioDocument,
    *,
    now: datetime,
    version: str,
) -> Built:
    """Validate the whole form and construct the assumptions, or explain each error.

    ``now`` is the valuation time t = 0 and the confirmation time. The valuation date
    fixes where forecasting may begin: year 1 must start on or after it.
    """
    errors: dict[str, str] = {}
    years = []
    day = now.astimezone(timezone.utc).date()
    try:
        periods = [year_period(form.first_year_end, n) for n in range(1, YEARS + 1)]
    except (ValueError, TypeError):
        periods = []
        errors[FIRST_YEAR_ERROR_KEY] = "Ungültiges Datum für das erste Prognosejahr."
    if periods and periods[0].start < day:
        errors[FIRST_YEAR_ERROR_KEY] = (
            f"Das erste Prognosejahr beginnt am {format_date(periods[0].start)}, also vor dem "
            f"Bewertungsstichtag {format_date(day)}. Das Jahresende muss so gewählt werden, dass "
            "das Jahr nach dem Stichtag beginnt."
        )
    rows = []
    for n in range(1, YEARS + 1):
        row = {
            "ebit": _read(form, f"y{n}.ebit", parse_money, errors),
            "tax": _read(
                form,
                f"y{n}.tax",
                parse_percent,
                errors,
                minimum=0,
                below=1,
                message="Der Steuersatz muss mindestens 0 % und unter 100 % liegen.",
            ),
            "da": _read(
                form,
                f"y{n}.da",
                parse_money,
                errors,
                minimum=0,
                message="Abschreibungen dürfen nicht negativ sein.",
            ),
            "capex": _read(
                form,
                f"y{n}.capex",
                parse_money,
                errors,
                minimum=0,
                message="Investitionen werden als positiver Betrag eingegeben (er wird abgezogen).",
            ),
            "dnwc": _read(form, f"y{n}.dnwc", parse_money, errors),
        }
        rows.append(row)
    wacc = _read(
        form,
        "wacc",
        parse_percent,
        errors,
        positive=True,
        message="Der WACC muss größer als 0 % sein.",
    )
    growth = _read(
        form,
        "growth",
        parse_percent,
        errors,
        minimum=0,
        message="Das nachhaltige Wachstum darf nicht negativ sein (mindestens 0 %).",
    )
    roic = _read(
        form,
        "roic",
        parse_percent,
        errors,
        positive=True,
        message="Der nachhaltige ROIC muss größer als 0 % sein.",
    )
    growth_problems = []
    if growth is not None and wacc is not None and growth >= wacc:
        growth_problems.append(
            "Das Wachstum g muss unter dem WACC liegen, sonst ist der Restwert nicht definiert."
        )
    if growth is not None and roic is not None and growth >= roic:
        growth_problems.append(
            "Das Wachstum g muss unter dem nachhaltigen ROIC liegen "
            "(Wachstum braucht Reinvestition)."
        )
    if growth_problems:
        errors["growth"] = " ".join(growth_problems)
    margin = None
    if form.text("margin").strip():
        margin = _read(
            form,
            "margin",
            parse_percent,
            errors,
            minimum=0,
            below=1,
            message="Der Sicherheitsabschlag muss mindestens 0 % und unter 100 % liegen.",
        )
    t_da = _read(
        form,
        "t.da",
        parse_money,
        errors,
        minimum=0,
        message="Abschreibungen dürfen nicht negativ sein.",
    )
    t_capex = _read(
        form,
        "t.capex",
        parse_money,
        errors,
        minimum=0,
        message="Investitionen werden als positiver Betrag eingegeben (er wird abgezogen).",
    )
    t_dnwc = _read(form, "t.dnwc", parse_money, errors)
    rationale = form.text("rationale").strip()
    terminal_rationale = form.text("t.rationale").strip()
    for field_id, text in (
        ("rationale", rationale),
        ("t.rationale", terminal_rationale),
    ):
        if not text:
            errors[field_id] = "Bitte eine kurze Begründung angeben."
        elif len(text) > MAX_TEXT:
            errors[field_id] = f"Höchstens {MAX_TEXT} Zeichen."

    help_ = terminal_help(form) if not errors else None
    nopat_6 = None
    if (
        not errors
        and help_ is not None
        and t_da is not None
        and t_capex is not None
        and t_dnwc is not None
    ):
        nopat_6 = help_.nopat_6
        net = t_capex + t_dnwc - t_da
        limit = PLAN_TOLERANCE * abs(nopat_6)
        if abs(net - help_.net_reinvestment) > limit:
            required = help_.required_capex
            hint = (
                f" Mit den übrigen Werten wäre Capex {format_money(required, 4)} {UNIT_NAME}."
                if required is not None and required >= 0
                else " Mit diesen Werten wäre Capex negativ: D&A oder ΔNWC des Folgejahres anpassen."
            )
            errors["t.capex"] = (
                f"Capex + ΔNWC − D&A muss die nötige Netto-Reinvestition NOPAT × g ÷ ROIC = "
                f"{format_money(help_.net_reinvestment, 4)} {UNIT_NAME} ergeben "
                f"(Toleranz 0,01 % von NOPAT des Folgejahres).{hint}"
            )
    if errors:
        return Built(None, errors, terminal_help(form))
    try:
        for n, row in enumerate(rows, 1):
            years.append(
                ForecastYear(
                    n,
                    periods[n - 1],
                    row["ebit"],
                    row["tax"],
                    row["da"],
                    row["capex"],
                    row["dnwc"],
                )
            )
        confirmed = form.confirmed
        assumptions = ScenarioAssumptions(
            document.inputs,
            version,
            scenario,
            now,
            document.originals.currency,
            tuple(years),
            TerminalPlan(nopat_6, t_da, t_capex, t_dnwc, terminal_rationale),
            wacc,
            growth,
            roic,
            margin,
            rationale,
            now if confirmed else None,
            APP_CONFIRMATION if confirmed else None,
        )
    except (ValueError, TypeError, DecimalException):
        return Built(None, {FORM_ERROR_KEY: GENERIC_ERROR}, help_)
    return Built(assumptions, {}, help_)


def content_key(assumptions: ScenarioAssumptions) -> tuple:
    """What a scenario says, without version, time and confirmation reference."""
    return (
        assumptions.inputs,
        assumptions.years,
        assumptions.terminal,
        assumptions.wacc,
        assumptions.terminal_growth,
        assumptions.sustainable_roic,
        assumptions.safety_margin,
        assumptions.rationale,
        assumptions.confirmed_at is not None,
    )


def preview_assumptions(assumptions: ScenarioAssumptions) -> ScenarioAssumptions:
    """Preview copy that counts as confirmed, so the arithmetic can be shown.

    Only the preview uses it; the saved scenario keeps the owner's confirmation
    choice and an unconfirmed scenario stays blocked in the overview.
    """
    if assumptions.confirmed_at is not None:
        return assumptions
    return replace(
        assumptions,
        confirmed_at=assumptions.valuation_at,
        confirmation_reference="preview-only",
    )


# --- manual supplements and normalizations ----------------------------------------


@dataclass(frozen=True, slots=True)
class ManualItem:
    """One observation of the original report set the owner may supplement/normalize."""

    key: str
    original: FundamentalObservation
    label: str
    purpose: ManualPurpose
    saved: ManualValue | None


def item_key(observation: FundamentalObservation) -> str:
    return f"{observation.field.value}|{period_label(observation.period)}"


def manual_items(document: ScenarioDocument) -> tuple[ManualItem, ...]:
    """Editable observations in the contract order, each with its saved value if any."""
    saved = {
        (m.adjustment.original.field, m.adjustment.original.period): m
        for m in document.manual_values
        if not m.needs_review(document.originals)
    }
    items = []
    for o in document.originals.observations:
        if o.field not in EDITABLE_FIELDS:
            continue
        purpose = (
            ManualPurpose.SUPPLEMENT
            if o.status is not ValueStatus.AVAILABLE
            else ManualPurpose.NORMALIZATION
        )
        items.append(
            ManualItem(
                item_key(o),
                o,
                f"{FIELD_NAMES[o.field]} · {period_label(o.period)}",
                purpose,
                saved.get((o.field, o.period)),
            )
        )
    return tuple(items)


def review_needed(document: ScenarioDocument) -> tuple[ManualValue, ...]:
    """Saved manual values that belong to an earlier report or changed original."""
    return tuple(
        m for m in document.manual_values if m.needs_review(document.originals)
    )


@dataclass(frozen=True, slots=True)
class ManualForm:
    value: str = ""
    reason: str = ""
    source: str = ""
    confirmed: bool = False
    approximation: bool = False


def read_manual_value(field: FundamentalField, text: str) -> Decimal:
    """Value in major units / pieces / fraction under the contract rule of the field."""
    kind = unit_kind(field)
    if kind is UnitKind.RATE:
        value = parse_percent(text)
        if not 0 <= value < 1:
            raise InputError(
                "Der Steuersatz muss mindestens 0 % und unter 100 % liegen."
            )
        return value
    value = parse_money(text)
    if field is FundamentalField.OUTSTANDING_SHARES and value <= 0:
        raise InputError("Die Aktienzahl muss größer als 0 sein.")
    if field not in _ANY_SIGN and value < 0:
        raise InputError("Dieser Wert darf nicht negativ sein.")
    return value


def build_manual(
    document: ScenarioDocument,
    item: ManualItem,
    form: ManualForm,
    *,
    now: datetime,
) -> tuple[ManualValue | None, dict[str, str]]:
    """Validate the form and build the manual value with its provenance."""
    errors: dict[str, str] = {}
    value = None
    try:
        value = read_manual_value(item.original.field, form.value)
    except InputError as exc:
        errors["value"] = str(exc)
    reason = form.reason.strip()
    source = form.source.strip()
    for key, text, message in (
        (
            "reason",
            reason,
            "Bitte eine kurze Begründung angeben (warum ersetzt dieser Wert den Anbieterwert?).",
        ),
        (
            "source",
            source,
            "Bitte einen Quellenhinweis angeben (Beleg, Dokument oder Berechnung); er bleibt in der privaten Ablage.",
        ),
    ):
        if not text:
            errors[key] = message
        elif len(text) > MAX_TEXT:
            errors[key] = f"Höchstens {MAX_TEXT} Zeichen."
    if errors:
        return None, errors
    original = item.original
    kind = unit_kind(original.field)
    unit = FundamentalUnit(
        kind,
        Decimal(1),
        document.originals.currency if kind is UnitKind.MONEY else None,
    )
    try:
        replacement = FundamentalObservation(
            original.field,
            value,
            ValueStatus.AVAILABLE,
            unit,
            original.period,
            original.report,
            FieldProvenance(
                document.originals.identity.source,
                EvidenceKind.MANUAL,
                source,
                f"manual entry for {original.field.value}",
                "owner input converted from millions to major units",
                None,
            ),
        )
        adjustment = ManualAdjustment(
            original,
            replacement,
            reason,
            now,
            now if form.confirmed else None,
            APP_CONFIRMATION if form.confirmed else None,
            form.approximation,
        )
        manual = ManualValue(
            InputReference.from_report_set(document.originals),
            adjustment,
            item.purpose,
            ManualPriority.MANUAL_WHEN_CONFIRMED,
        )
    except (ValueError, TypeError, DecimalException):
        return None, {FORM_ERROR_KEY: GENERIC_ERROR}
    return manual, {}


# --- document operations ------------------------------------------------------------


def new_document(report_set: FundamentalReportSet, now: datetime) -> ScenarioDocument:
    """First (unsaved) state for a security: originals only, no plan, no manual value."""
    return ScenarioDocument(report_set, report_set.input_version, MODEL_VERSION, now)


def next_data_version(
    document: ScenarioDocument, originals: FundamentalReportSet | None = None
) -> str:
    """Unique in the lineage of one security: input version plus a running counter.

    The counter continues the current data version, so chained edits that are not
    saved in between never name two different bases with one version.
    """
    base = (originals or document.originals).input_version
    match = re.search(r"/d([0-9]+)$", document.data_version)
    return f"{base}/d{int(match.group(1)) + 1 if match else 1}"


def next_assumptions_version(document: ScenarioDocument) -> str:
    return f"a{document.revision + 1}"


def add_manual(
    document: ScenarioDocument, manual: ManualValue, now: datetime
) -> ScenarioDocument:
    return document.with_manual(
        manual, data_version=next_data_version(document), changed_at=now
    )


def reset_manual(
    document: ScenarioDocument, manual: ManualValue, now: datetime
) -> ScenarioDocument:
    return document.reset_manual(
        manual, data_version=next_data_version(document), changed_at=now
    )


def adopt_report_set(
    document: ScenarioDocument, report_set: FundamentalReportSet, now: datetime
) -> ScenarioDocument:
    """Take a newer original report set; manual values and plans keep their old binding."""
    return document.refresh(
        report_set,
        data_version=next_data_version(document, report_set),
        changed_at=now,
    )


def save_scenario(
    document: ScenarioDocument, assumptions: ScenarioAssumptions, now: datetime
) -> ScenarioDocument:
    return document.with_scenario(assumptions, changed_at=now)


# --- display: history, sources ----------------------------------------------------


def annual_periods(report_set: FundamentalReportSet) -> tuple[FlowPeriod, ...]:
    return tuple(
        sorted(
            {
                o.period
                for o in report_set.observations
                if o.field is FundamentalField.EBIT
                and isinstance(o.period, FlowPeriod)
                and o.period.kind is PeriodKind.FISCAL_YEAR
                and o.period.complete
            },
            key=lambda p: p.start,
        )
    )


def _effective(report_set: FundamentalReportSet) -> dict:
    return {(o.field, o.period): o for o in report_set.effective_observations}


def _cell(observation: FundamentalObservation | None, currency: str) -> str:
    if observation is None:
        return "fehlt"
    text = format_observation(observation, currency)
    if observation.provenance.kind is EvidenceKind.MANUAL:
        text += " *"
    return text


def historical_fcff(
    effective: Mapping, period: FlowPeriod
) -> tuple[Decimal | None, Decimal | None]:
    """(ΔNWC, FCFF) shown for one year from the effective inputs; None when a part is missing.

    The same equation as ``dcf`` (EBIT·(1−T) + D&A − Capex − ΔNWC with ΔNWC from the two
    balance dates); ``dcf`` stays authoritative, this is only the display of the history.
    """

    def get(field, when):
        o = effective.get((field, when))
        return None if o is None else absolute(o)

    ebit = get(FundamentalField.EBIT, period)
    rate = get(FundamentalField.OPERATING_TAX_RATE, period)
    da = get(FundamentalField.DEPRECIATION_AMORTIZATION, period)
    capex = get(FundamentalField.CAPEX, period)
    opening = get(
        FundamentalField.OPERATING_NWC, InstantPeriod(period.start - timedelta(days=1))
    )
    closing = get(FundamentalField.OPERATING_NWC, InstantPeriod(period.end))
    change = None if opening is None or closing is None else closing - opening
    if None in (ebit, rate, da, capex, change):
        return change, None
    return change, ebit * (1 - rate) + da - capex - change


@dataclass(frozen=True, slots=True)
class TableData:
    """Plain text table: header cells and rows of equal width."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def as_records(self) -> list[dict[str, str]]:
        return [dict(zip(self.header, row, strict=True)) for row in self.rows]


def history_table(report_set: FundamentalReportSet) -> TableData:
    """Effective history per fiscal year; ``*`` marks a manual value, 'fehlt' a gap."""
    currency = report_set.currency
    effective = _effective(report_set)
    periods = annual_periods(report_set)
    header = ("Kennzahl", *(f"GJ bis {format_date(p.end)}" for p in periods))
    rows = []
    for field_, name in (
        (FundamentalField.EBIT, "EBIT"),
        (FundamentalField.OPERATING_TAX_RATE, "Steuersatz"),
        (FundamentalField.DEPRECIATION_AMORTIZATION, "D&A"),
        (FundamentalField.CAPEX, "Capex"),
    ):
        rows.append(
            (name, *(_cell(effective.get((field_, p)), currency) for p in periods))
        )
    rows.append(
        (
            "NWC Jahresende",
            *(
                _cell(
                    effective.get(
                        (FundamentalField.OPERATING_NWC, InstantPeriod(p.end))
                    ),
                    currency,
                )
                for p in periods
            ),
        )
    )
    changes = [historical_fcff(effective, p) for p in periods]
    rows.append(("ΔNWC (abgeleitet)", *(format_money(c) for c, _ in changes)))
    rows.append(("FCFF (abgeleitet)", *(format_money(f) for _, f in changes)))
    return TableData(header, tuple(rows))


def bridge_table(report_set: FundamentalReportSet) -> TableData:
    """Capital bridge and share count with their dates; manual values are marked."""
    currency = report_set.currency
    effective = _effective(report_set)
    at_bridge = InstantPeriod(report_set.bridge_date)
    rows = []
    for field_, sign in (
        (FundamentalField.EXCESS_CASH, "+"),
        (FundamentalField.NONOPERATING_ASSETS, "+"),
        (FundamentalField.FINANCIAL_DEBT, "−"),
        (FundamentalField.PREFERRED_CLAIMS, "−"),
        (FundamentalField.MINORITY_CLAIMS, "−"),
    ):
        rows.append(
            (
                f"{sign} {FIELD_NAMES[field_]}",
                _cell(effective.get((field_, at_bridge)), currency),
                format_date(report_set.bridge_date),
            )
        )
    for field_ in (
        FundamentalField.OUTSTANDING_SHARES,
        FundamentalField.INCREMENTAL_DILUTION,
    ):
        rows.append(
            (
                FIELD_NAMES[field_],
                _cell(effective.get((field_, report_set.shares_period)), currency),
                format_date(report_set.shares_period.as_of),
            )
        )
    return TableData(("Position", "Wert", "Stichtag"), tuple(rows))


def source_lines(
    document: ScenarioDocument, effective: FundamentalReportSet
) -> tuple[str, ...]:
    """Every model input with period, source, retrieval and, if manual, its provenance."""
    currency = effective.currency
    originals = {(o.field, o.period): o for o in document.originals.observations}
    lines = []
    for o in effective.effective_observations:
        if o.field not in EDITABLE_FIELDS:
            continue
        head = f"{FIELD_NAMES[o.field]} · {period_label(o.period)}: {format_observation(o, currency)}"
        p = o.provenance
        if p.kind is EvidenceKind.MANUAL:
            adjustment = next(
                (
                    m.adjustment
                    for m in document.manual_values
                    if m.adjustment.replacement == o
                ),
                None,
            )
            original = originals.get((o.field, o.period))
            detail = [f"manuell, Quelle: {p.field_reference}"]
            if adjustment is not None:
                detail.append(f"Begründung: {adjustment.reason}")
                detail.append(f"geändert {format_date(adjustment.changed_at)}")
                if adjustment.confirmed_at is not None:
                    detail.append(f"bestätigt {format_date(adjustment.confirmed_at)}")
            if original is not None:
                detail.append(f"Original: {format_observation(original, currency)}")
        else:
            detail = [f"Anbieter {p.source.provider}, Zeile „{p.field_reference}“"]
            if p.retrieved_at is not None:
                detail.append(f"abgerufen {format_date(p.retrieved_at)}")
            detail.append(
                "Veröffentlichung unbekannt"
                if p.published_at is None
                else f"veröffentlicht {format_date(p.published_at)}"
            )
        lines.append(f"{head} ({'; '.join(detail)})")
    return tuple(lines)


def manual_lines(
    document: ScenarioDocument,
) -> tuple[tuple[ManualValue, str], ...]:
    """Saved manual values with a plain description (original and replacement kept)."""
    currency = document.originals.currency
    result = []
    for m in document.manual_values:
        a = m.adjustment
        status = []
        if m.needs_review(document.originals):
            status.append(
                "prüfbedürftig: gehört zu einem früheren Bericht, wird nicht angewendet"
            )
        elif a.confirmed_at is None:
            status.append("unbestätigt, wird nicht angewendet")
        else:
            status.append(f"bestätigt am {format_date(a.confirmed_at)}, hat Vorrang")
        if a.book_value_approximation:
            status.append("Buchwert als Näherung")
        purpose = (
            "Normalisierung"
            if m.purpose is ManualPurpose.NORMALIZATION
            else "Ergänzung"
        )
        result.append(
            (
                m,
                f"{FIELD_NAMES[a.original.field]} · {period_label(a.original.period)}: "
                f"{purpose} {format_observation(a.replacement, currency)} statt "
                f"{format_observation(a.original, currency)} ({'; '.join(status)}; "
                f"Begründung: {a.reason}; Quelle: {a.replacement.provenance.field_reference})",
            )
        )
    return tuple(result)


# --- display: calculation path -----------------------------------------------------


def _step(result: IntrinsicValueResult, name: str) -> CalculationStep | None:
    return next((s for s in result.steps if s.name == name), None)


def _value(result: IntrinsicValueResult, name: str) -> Decimal | None:
    step = _step(result, name)
    return None if step is None else step.value


@dataclass(frozen=True, slots=True)
class CalculationPath:
    """The visible arithmetic of one scenario result (unrounded values, rounded text)."""

    years: TableData
    terminal: TableData
    bridge: TableData
    value_lines: TableData
    terminal_share: str
    dominance_hint: str | None
    available: bool


def calculation_path(
    assumptions: ScenarioAssumptions,
    result: IntrinsicValueResult,
    limitations: Sequence[str],
) -> CalculationPath:
    """FCFF forecasts, present values, terminal value share, bridge, shares, value."""
    currency = result.currency
    header = ("Position", *(f"J{n}" for n in range(1, YEARS + 1)))

    def row(label, values):
        return (label, *values)

    nopat = [
        None
        if y.ebit is None or y.operating_tax_rate is None
        else y.ebit * (1 - y.operating_tax_rate)
        for y in assumptions.years
    ]
    wacc = assumptions.wacc
    year_rows = [
        row("GJ bis", (format_date(y.period.end) for y in assumptions.years)),
        row("EBIT", (format_money(y.ebit) for y in assumptions.years)),
        row(
            "Steuersatz",
            (format_percent(y.operating_tax_rate) for y in assumptions.years),
        ),
        row("NOPAT", (format_money(n) for n in nopat)),
        row(
            "+ D&A",
            (format_money(y.depreciation_amortization) for y in assumptions.years),
        ),
        row("− Capex", (format_money(y.capex) for y in assumptions.years)),
        row(
            "− ΔNWC", (format_money(y.change_operating_nwc) for y in assumptions.years)
        ),
        row(
            "= FCFF",
            (
                format_money(_value(result, f"fcff_year_{n}"))
                for n in range(1, YEARS + 1)
            ),
        ),
        row(
            "Diskontfaktor",
            (
                NOT_AVAILABLE if wacc is None else _german(1 / (1 + wacc) ** n, 4)
                for n in range(1, YEARS + 1)
            ),
        ),
        row(
            "Barwert",
            (format_money(_value(result, f"pv_year_{n}")) for n in range(1, YEARS + 1)),
        ),
    ]
    pv_sum = _value(result, "pv_explicit_sum")
    share = result.terminal_value_share
    dominance = (
        "Terminalwertdominanz: Der Restwert trägt 75 % oder mehr des operativen Werts."
        if "terminal_value_dominant" in limitations
        else None
    )
    terminal = (
        ("Summe der Barwerte Jahre 1–5", format_money(pv_sum)),
        (
            "NOPAT Folgejahr = NOPAT J5 × (1 + g)",
            format_money(_value(result, "nopat_6")),
        ),
        (
            "− Netto-Reinvestition = NOPAT × g ÷ ROIC",
            format_money(_value(result, "reinvestment_6")),
        ),
        (
            "= Nachhaltiger FCFF Folgejahr",
            format_money(_value(result, "sustainable_fcff_6")),
        ),
        (
            "Restwert am Ende von Jahr 5 = FCFF ÷ (WACC − g)",
            format_money(_value(result, "terminal_value_5")),
        ),
        ("Barwert des Restwerts", format_money(_value(result, "pv_terminal_value"))),
        (
            "Operativer Wert = Barwerte + Barwert Restwert",
            format_money(_value(result, "operating_value")),
        ),
    )
    bridge = (
        ("Operativer Wert", format_money(_value(result, "operating_value"))),
        (
            "+ Überschüssige liquide Mittel",
            format_money(_value(result, "bridge_excess_cash")),
        ),
        (
            "+ Nichtoperative Vermögenswerte",
            format_money(_value(result, "bridge_nonoperating_assets")),
        ),
        ("− Finanzschulden", format_money(_value(result, "bridge_financial_debt"))),
        ("− Vorzugsansprüche", format_money(_value(result, "bridge_preferred_claims"))),
        (
            "− Minderheitsansprüche",
            format_money(_value(result, "bridge_minority_claims")),
        ),
        (
            "= Wert des Eigenkapitals der Stammaktionäre",
            format_money(_value(result, "common_equity_value")),
        ),
    )
    shares = _value(result, "diluted_shares")
    value_lines = (
        (
            "Verwässerte Aktienzahl (ausstehend + Verwässerung)",
            format_money(shares) + " Mio. Stück"
            if shares is not None
            else NOT_AVAILABLE,
        ),
        (
            "Wert je Aktie = Eigenkapital ÷ Aktienzahl",
            format_per_share(result.value_per_share, currency),
        ),
        (
            "Sicherheitsabschlag (persönlich)",
            format_percent(assumptions.safety_margin),
        ),
        (
            "Zielpreis = Wert je Aktie × (1 − Abschlag)",
            format_per_share(result.target_price, currency),
        ),
    )
    return CalculationPath(
        TableData(header, tuple(year_rows)),
        TableData(("Schritt", f"Mio. {currency}"), terminal),
        TableData(("Eigenkapitalbrücke", f"Mio. {currency}"), bridge),
        TableData(("Aktienzahl und Wert", "Ergebnis"), value_lines),
        format_percent(share) if share is not None else NOT_AVAILABLE,
        dominance,
        result.value_per_share is not None,
    )


def limitation_texts(limitations: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(LIMITATION_TEXT.get(code, code) for code in limitations))


# --- display: sensitivity ---------------------------------------------------------

WACC_STEPS = (Decimal("0.005"), Decimal("0.01"), Decimal("0.02"))
GROWTH_STEPS = (Decimal("0.0025"), Decimal("0.005"), Decimal("0.01"))
AXIS_RADIUS = 2
_CELL_REASON = {
    "wacc_not_positive": "WACC ≤ 0: nicht berechenbar",
    "growth_negative": "g < 0: im Modell nicht zulässig",
    "growth_not_below_wacc": "g ≥ WACC: Restwert nicht definiert",
    "growth_not_below_roic": "g ≥ ROIC: Wachstum ohne tragfähige Reinvestition",
    "roic_not_positive": "ROIC ≤ 0: nicht berechenbar",
    "nonpositive_sustainable_fcff": "nachhaltiger FCFF ≤ 0",
    "no_positive_operating_value": "operativer Wert ≤ 0",
    "no_positive_common_equity_value": "Eigenkapitalwert ≤ 0: kein Wert je Aktie",
    "calculation_overflow": "Zahlenbereich überschritten",
}


@dataclass(frozen=True, slots=True)
class SensitivityView:
    table: TableData | None
    notes: tuple[str, ...]
    blocked: tuple[str, ...]


def sensitivity_view(
    report_set: FundamentalReportSet,
    assumptions: ScenarioAssumptions,
    wacc_step: Decimal,
    growth_step: Decimal,
) -> SensitivityView:
    """5 × 5 grid around the scenario's WACC and g; invalid cells carry a numbered reason.

    The centre cell (●) is the scenario itself. Steps are display parameters, not
    assumptions. A blocked scenario yields no grid, only its reasons.
    """
    if assumptions.wacc is None or assumptions.terminal_growth is None:
        return SensitivityView(
            None,
            (),
            (
                explain_issue(
                    QualityIssue(QualityStatus.INCOMPLETE, "scenario_components")
                ),
            ),
        )
    wacc_axis = centered_axis(assumptions.wacc, wacc_step, AXIS_RADIUS)
    growth_axis = centered_axis(assumptions.terminal_growth, growth_step, AXIS_RADIUS)
    grid: SensitivityGrid = calculate_sensitivity(
        report_set, assumptions, wacc_axis, growth_axis
    )
    blocking = tuple(i for i in grid.issues if i.blocking)
    if blocking:
        return SensitivityView(None, (), blocking_texts(blocking))
    notes: list[str] = []
    rows = []
    for r, (wacc, cells) in enumerate(zip(grid.wacc_values, grid.rows, strict=True)):
        texts = []
        for c, cell in enumerate(cells):
            centre = r == AXIS_RADIUS and c == AXIS_RADIUS
            if cell.value_per_share is not None:
                text = _german(cell.value_per_share, 2)
            else:
                reasons = [
                    _CELL_REASON.get(i.code, i.code) for i in cell.issues if i.blocking
                ]
                reason = (
                    f"WACC {format_percent(cell.wacc)}, g {format_percent(cell.growth)}: "
                    + "; ".join(dict.fromkeys(reasons))
                )
                if reason not in notes:
                    notes.append(reason)
                text = f"n. v. ({notes.index(reason) + 1})"
            texts.append(text + (" ●" if centre else ""))
        rows.append((f"WACC {format_percent(wacc)}", *texts))
    header = ("", *(f"g {format_percent(g)}" for g in grid.growth_values))
    numbered = tuple(f"({i}) {n}" for i, n in enumerate(notes, 1))
    return SensitivityView(TableData(header, tuple(rows)), numbered, ())


def now_utc_date(now: datetime) -> date:
    return now.astimezone(timezone.utc).date()


def preview_calculation(document: ScenarioDocument, assumptions: ScenarioAssumptions):
    """Calculation of the preview copy on the effective input of the document."""
    return calculate_scenario(
        document.effective_report_set(), preview_assumptions(assumptions)
    )
