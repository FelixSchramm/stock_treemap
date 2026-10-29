"""Detail view building blocks (#80) with invented numbers; offline, no UI code.

The reference figures are those of docs/INTRINSIC_VALUE.md (127,650095311423 per
share in the base case), so the form-to-assumptions path is checked against the
independently derived example and not against its own formulas.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from test_dcf import base_report
from test_scenarios import repository

import portfolio_dashboard.valuation_detail as detail
from portfolio_dashboard.dcf import MODEL_VERSION, calculate_scenario
from portfolio_dashboard.fundamentals import (
    FundamentalField,
    QualityIssue,
    QualityStatus,
)
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.models import ValueStatus
from portfolio_dashboard.scenarios import ManualPurpose, ScenarioConflict

D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=1)


def document(report=None):
    report = report or base_report()
    return detail.new_document(report, NOW)


def form_values(**overrides):
    """The base scenario of docs/INTRINSIC_VALUE.md as owner text (millions, percent)."""
    values = {}
    for n, ebit in enumerate((160, 170, 180, 190, 200), 1):
        values.update(
            {
                f"y{n}.ebit": str(ebit),
                f"y{n}.tax": "20",
                f"y{n}.da": "20",
                f"y{n}.capex": "48",
                f"y{n}.dnwc": "20",
            }
        )
    values.update(
        {
            "wacc": "10",
            "growth": "2",
            "roic": "10",
            "t.da": "20",
            "t.capex": "52,64",
            "t.dnwc": "0",
            "rationale": "invented plan",
            "t.rationale": "invented sustainable year",
        }
    )
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def form(confirmed=True, first=date(2026, 12, 31), **overrides):
    return detail.ScenarioForm(form_values(**overrides), first, confirmed)


def build(doc=None, **kwargs):
    doc = doc or document()
    scenario = kwargs.pop("scenario", Scenario.BASE)
    return detail.build_assumptions(
        kwargs.pop("form", None) or form(**kwargs),
        scenario,
        doc,
        now=NOW,
        version=detail.next_assumptions_version(doc),
    )


# --- parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12,5", "12.5"), ("12.5", "12.5"), (" -3 ", "-3"), ("0", "0"), ("+4,25", "4.25")],
)
def test_numbers_accept_german_and_plain_decimals(text, expected):
    assert detail.parse_number(text) == D(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "abc",
        "1e3",
        "1.234,5",
        "1,2,3",
        "NaN",
        "inf",
        "12 %",
        "1 000",
        "٣",
        "1" * 14,
        "0,1234567890123",
    ],
)
def test_malformed_numbers_are_explained_and_never_accepted(text):
    with pytest.raises(detail.InputError) as error:
        detail.parse_number(text)
    assert text.strip() not in str(error.value) or not text.strip()


def test_units_percent_and_millions_convert_exactly():
    assert detail.parse_percent("12,5 %") == D("0.125")
    assert detail.parse_percent("12,5") == D("0.125")
    assert detail.parse_money("1,5") == D(1_500_000)
    assert detail.money_input(D(160_000_000)) == "160"
    assert detail.money_input(D("52640000")) == "52,64"
    assert detail.percent_input(D("0.125")) == "12,5"
    assert detail.money_input(None) == "" and detail.percent_input(None) == ""
    assert detail.format_money(None) == "n. v."  # missing is never zero


def test_add_years_handles_leap_day():
    assert detail.add_years(date(2028, 2, 29), 1) == date(2029, 2, 28)
    assert detail.add_years(date(2026, 12, 31), -1) == date(2025, 12, 31)


def test_default_first_year_starts_on_or_after_the_valuation_date():
    doc = document()
    assert detail.default_first_year_end(doc.originals, date(2026, 1, 1)) == date(
        2026, 12, 31
    )
    # A later valuation date moves to the next fiscal year; nothing is invented.
    assert detail.default_first_year_end(doc.originals, date(2026, 6, 1)) == date(
        2027, 12, 31
    )


# --- scenario form -> assumptions ----------------------------------------------------------


def test_valid_form_reproduces_the_documented_reference_value():
    doc = document()
    built = build(doc)
    assert built.errors == {} and built.assumptions is not None
    a = built.assumptions
    assert a.inputs == doc.inputs and a.currency == "EUR" and a.version == "a1"
    assert a.confirmed_at == NOW and a.valuation_at == NOW
    assert a.years[0].period.start == date(2026, 1, 1)
    assert a.years[4].period.end == date(2030, 12, 31)
    assert a.terminal.nopat == D("163.2") * detail.UNIT  # NOPAT_5 x (1 + g), derived
    saved = detail.save_scenario(doc, a, NOW)
    calc = calculate_scenario(saved.effective_report_set(), a)
    assert not [i for i in calc.result.issues if i.blocking]
    assert abs(calc.result.value_per_share - D("127.650095311423")) < D("1e-12")


def test_safety_margin_is_optional_and_gives_the_documented_target_price():
    doc = document()
    assert build(doc).assumptions.safety_margin is None
    a = build(doc, margin="20").assumptions
    assert a.safety_margin == D("0.2")
    calc = calculate_scenario(
        detail.save_scenario(doc, a, NOW).effective_report_set(), a
    )
    assert abs(calc.result.target_price - D("102.120076249138")) < D("1e-11")
    assert calc.result.value_per_share > calc.result.target_price


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"y1.ebit": ""}, "y1.ebit"),
        ({"y2.ebit": "abc"}, "y2.ebit"),
        ({"y3.tax": "100"}, "y3.tax"),
        ({"y3.tax": "-1"}, "y3.tax"),
        ({"y4.da": "-5"}, "y4.da"),
        ({"y5.capex": "-1"}, "y5.capex"),
        ({"y5.dnwc": "1e3"}, "y5.dnwc"),
        ({"wacc": "0"}, "wacc"),
        ({"wacc": ""}, "wacc"),
        ({"growth": "-1"}, "growth"),
        ({"growth": "10"}, "growth"),  # g = WACC
        ({"growth": "12"}, "growth"),  # g > WACC
        ({"roic": "0"}, "roic"),
        ({"roic": "1,5"}, "growth"),  # g >= ROIC
        ({"margin": "100"}, "margin"),
        ({"margin": "-5"}, "margin"),
        ({"margin": "x"}, "margin"),
        ({"t.da": "-1"}, "t.da"),
        ({"t.capex": ""}, "t.capex"),
        ({"rationale": "  "}, "rationale"),
        ({"t.rationale": ""}, "t.rationale"),
        ({"rationale": "x" * 501}, "rationale"),
    ],
)
def test_invalid_input_is_explained_at_the_field_and_builds_nothing(overrides, field):
    built = build(**overrides)
    assert built.assumptions is None
    assert field in built.errors and built.errors[field]
    for message in built.errors.values():
        assert not any(bad in message for bad in ("Traceback", "Exception"))


def test_a_missing_field_is_never_defaulted_to_zero():
    values = form_values()
    del values["wacc"]
    built = build(form=detail.ScenarioForm(values, date(2026, 12, 31), True))
    assert built.assumptions is None and "wacc" in built.errors


def test_terminal_plan_that_contradicts_g_and_roic_names_the_required_capex():
    built = build(**{"t.capex": "40"})
    assert built.assumptions is None
    message = built.errors["t.capex"]
    assert "32,64" in message and "52,6400" in message  # required reinvestment / capex
    assert built.terminal.net_reinvestment == D("32.64") * detail.UNIT
    assert built.terminal.required_capex == D("52.64") * detail.UNIT


def test_first_forecast_year_may_not_begin_before_the_valuation_date():
    built = build(first=date(2025, 12, 31))
    assert built.assumptions is None
    assert "Bewertungsstichtag" in built.errors[detail.FIRST_YEAR_ERROR_KEY]


def test_unconfirmed_assumptions_are_saved_as_such_and_block_the_value():
    doc = document()
    a = build(doc, confirmed=False).assumptions
    assert a.confirmed_at is None and a.confirmation_reference is None
    saved = detail.save_scenario(doc, a, NOW)
    blocking = {i.code for i in saved.issues_for(Scenario.BASE) if i.blocking}
    assert "scenario_confirmation" in blocking
    assert (
        calculate_scenario(saved.effective_report_set(), a).result.value_per_share
        is None
    )
    # The preview copy only exists to show the arithmetic.
    preview = detail.preview_calculation(saved, a)
    assert preview.result.value_per_share is not None
    assert a.confirmed_at is None


def test_confirmation_is_not_carried_over_to_other_data():
    doc = document()
    first = detail.save_scenario(doc, build(doc).assumptions, NOW)
    seed = detail.form_from_saved(first.scenario(Scenario.BASE), first, NOW.date())
    assert seed.confirmed is True
    assert seed.text("wacc") == "10" and seed.text("y1.ebit") == "160"
    assert seed.text("t.capex") == "52,64" and seed.first_year_end == date(2026, 12, 31)
    # New data basis -> the old confirmation does not apply to it.
    changed = replace(first, data_version="other-basis")
    again = detail.form_from_saved(changed.scenario(Scenario.BASE), changed, NOW.date())
    assert again.confirmed is False and again.text("wacc") == "10"
    assert detail.form_from_saved(None, doc, NOW.date()).values == {}


def test_content_key_ignores_version_and_time_but_not_numbers():
    doc = document()
    a = build(doc).assumptions
    assert detail.content_key(a) == detail.content_key(replace(a, version="x"))
    assert detail.content_key(a) != detail.content_key(build(doc, wacc="9").assumptions)


# --- manual supplements and normalizations -----------------------------------------------


def missing_tax_report():
    report = base_report()
    period = next(
        o.period
        for o in report.observations
        if o.field is FundamentalField.OPERATING_TAX_RATE
    )
    observations = tuple(
        replace(o, value=None, status=ValueStatus.MISSING)
        if (o.field, o.period) == (FundamentalField.OPERATING_TAX_RATE, period)
        else o
        for o in report.observations
    )
    return replace(report, observations=observations), period


def tax_item(doc, period):
    return next(
        i
        for i in detail.manual_items(doc)
        if i.original.field is FundamentalField.OPERATING_TAX_RATE
        and i.original.period == period
    )


def test_items_cover_model_inputs_only_and_name_supplement_or_normalization():
    report, period = missing_tax_report()
    doc = document(report)
    items = detail.manual_items(doc)
    fields = {i.original.field for i in items}
    assert FundamentalField.PROVIDER_FCF not in fields
    assert FundamentalField.WEIGHTED_EPS_SHARES not in fields
    assert FundamentalField.CHANGE_OPERATING_NWC not in fields
    assert tax_item(doc, period).purpose is ManualPurpose.SUPPLEMENT
    ebit = next(i for i in items if i.original.field is FundamentalField.EBIT)
    assert ebit.purpose is ManualPurpose.NORMALIZATION
    assert "Periode" not in ebit.label and "Geschäftsjahr" in ebit.label


def test_supplement_needs_value_reason_and_source_and_keeps_the_original():
    report, period = missing_tax_report()
    doc = document(report)
    item = tax_item(doc, period)
    manual, errors = detail.build_manual(doc, item, detail.ManualForm(), now=NOW)
    assert manual is None and set(errors) == {"value", "reason", "source"}
    for bad in ("100", "-1", "abc", ""):
        _, errors = detail.build_manual(
            doc, item, detail.ManualForm(bad, "r", "s"), now=NOW
        )
        assert "value" in errors
    manual, errors = detail.build_manual(
        doc,
        item,
        detail.ManualForm("21,5", "Grund", "Beleg 1", confirmed=True),
        now=NOW,
    )
    assert errors == {} and manual.adjustment.replacement.value == D("0.215")
    assert manual.adjustment.original == item.original  # original stays untouched
    assert manual.adjustment.confirmed_at == NOW
    updated = detail.add_manual(doc, manual, LATER)
    assert updated.data_version != doc.data_version
    assert updated.originals == doc.originals
    effective = {
        (o.field, o.period): o
        for o in updated.effective_report_set().effective_observations
    }
    assert effective[(FundamentalField.OPERATING_TAX_RATE, period)].value == D("0.215")
    # Reset restores the automatic source (the gap), the history keeps the supplement.
    reset = detail.reset_manual(updated, manual, LATER)
    effective = {
        (o.field, o.period): o
        for o in reset.effective_report_set().effective_observations
    }
    assert (
        effective[(FundamentalField.OPERATING_TAX_RATE, period)].status
        is ValueStatus.MISSING
    )
    assert reset.manual_values == () and reset.data_version != updated.data_version


def test_unconfirmed_manual_value_is_stored_but_not_applied():
    report, period = missing_tax_report()
    doc = document(report)
    manual, _ = detail.build_manual(
        doc, tax_item(doc, period), detail.ManualForm("20", "g", "q"), now=NOW
    )
    updated = detail.add_manual(doc, manual, LATER)
    assert manual.adjustment.confirmed_at is None
    effective = {
        (o.field, o.period): o
        for o in updated.effective_report_set().effective_observations
    }
    assert (
        effective[(FundamentalField.OPERATING_TAX_RATE, period)].status
        is ValueStatus.MISSING
    )
    codes = {i.code for i in updated.effective_report_set().issues_at(LATER)}
    assert "manual_confirmation" in codes
    (line,) = [t for _, t in detail.manual_lines(updated)]
    assert "unbestätigt" in line and "Begründung: g" in line


def test_shares_and_bridge_fields_follow_their_contract_rules():
    doc = document()
    items = {i.original.field: i for i in detail.manual_items(doc)}
    shares = items[FundamentalField.OUTSTANDING_SHARES]
    _, errors = detail.build_manual(
        doc, shares, detail.ManualForm("0", "r", "s"), now=NOW
    )
    assert "value" in errors
    debt = items[FundamentalField.FINANCIAL_DEBT]
    _, errors = detail.build_manual(
        doc, debt, detail.ManualForm("-1", "r", "s"), now=NOW
    )
    assert "value" in errors
    ebit = next(
        i for i in detail.manual_items(doc) if i.original.field is FundamentalField.EBIT
    )
    manual, errors = detail.build_manual(
        doc, ebit, detail.ManualForm("-5", "r", "s", True, True), now=NOW
    )
    assert errors == {} and manual.adjustment.book_value_approximation


def test_new_report_flags_manual_values_and_scenarios_for_review():
    report, period = missing_tax_report()
    doc = document(report)
    manual, _ = detail.build_manual(
        doc, tax_item(doc, period), detail.ManualForm("20", "g", "q", True), now=NOW
    )
    doc = detail.add_manual(doc, manual, NOW)
    doc = detail.save_scenario(doc, build(doc).assumptions, NOW)
    newer = replace(report, input_version="newer-report")
    adopted = detail.adopt_report_set(doc, newer, LATER)
    assert adopted.originals.input_version == "newer-report"
    assert detail.review_needed(adopted) == (manual,)
    (line,) = [t for _, t in detail.manual_lines(adopted)]
    assert "prüfbedürftig" in line
    codes = {i.code for i in adopted.issues_for(Scenario.BASE) if i.blocking}
    assert {"assumptions_input_mismatch", "manual_review_required"} <= codes
    # Nothing was silently carried over: the stored plan keeps its old binding.
    assert adopted.scenario(Scenario.BASE).assumptions.inputs != adopted.inputs
    # The editor may re-save only after an explicit new build on the new data.
    rebuilt = build(adopted)
    assert rebuilt.assumptions.inputs == adopted.inputs
    assert rebuilt.assumptions.confirmed_at == NOW


def test_versions_stay_unique_across_edits_and_the_repository_accepts_them(tmp_path):
    repo = repository(tmp_path)
    report, period = missing_tax_report()
    doc = document(report)
    identity = doc.originals.identity
    manual, _ = detail.build_manual(
        doc, tax_item(doc, period), detail.ManualForm("20", "g", "q", True), now=NOW
    )
    doc = repo.save(detail.add_manual(doc, manual, NOW))
    doc = repo.save(detail.save_scenario(doc, build(doc).assumptions, NOW))
    doc = repo.save(detail.save_scenario(doc, build(doc, wacc="9").assumptions, LATER))
    doc = repo.save(detail.reset_manual(doc, doc.manual_values[0], LATER))
    assert [doc.revision] == [4]
    reloaded = repo.get(identity)
    assert reloaded == doc and reloaded.manual_values == ()
    assert repo.get(identity, revision=1).manual_values[0] == manual  # history kept


def test_two_competing_editors_get_a_conflict_not_a_lost_update(tmp_path):
    repo = repository(tmp_path)
    doc = repo.save(document())
    one = detail.save_scenario(doc, build(doc).assumptions, NOW)
    two = detail.save_scenario(doc, build(doc, version=None, wacc="9").assumptions, NOW)
    repo.save(one)
    with pytest.raises(ScenarioConflict):
        repo.save(two)
    assert repo.get(doc.originals.identity).scenario(Scenario.BASE) == one.scenario(
        Scenario.BASE
    )


# --- display ------------------------------------------------------------------------------


def test_history_table_shows_gaps_manual_marks_and_derived_fcff_matches_dcf():
    report, period = missing_tax_report()
    doc = document(report)
    table = detail.history_table(doc.effective_report_set())
    row = {r[0]: r for r in table.rows}
    column = table.header.index(f"GJ bis {detail.format_date(period.end)}")
    assert row["Steuersatz"][column] == "fehlt"
    assert row["FCFF (abgeleitet)"][column] == "n. v."  # a gap, never zero
    manual, _ = detail.build_manual(
        doc, tax_item(doc, period), detail.ManualForm("20", "g", "q", True), now=NOW
    )
    full = detail.add_manual(doc, manual, NOW)
    table = detail.history_table(full.effective_report_set())
    row = {r[0]: r for r in table.rows}
    assert row["Steuersatz"][column] == "20,00 % *"
    # The displayed history equals the calculator's own historical FCFF.
    withplan = detail.save_scenario(full, build(full).assumptions, NOW)
    calc = calculate_scenario(withplan.effective_report_set(), build(full).assumptions)
    steps = {
        s.name: s.value
        for s in calc.result.steps
        if s.name.startswith("historical_fcff_")
    }
    shown = {
        p: detail.historical_fcff(
            {
                (o.field, o.period): o
                for o in full.effective_report_set().effective_observations
            },
            p,
        )[1]
        for p in detail.annual_periods(full.effective_report_set())
    }
    assert sorted(steps.values()) == sorted(shown.values())


def test_bridge_table_and_sources_name_period_source_and_unknown_publication():
    doc = document()
    bridge = detail.bridge_table(doc.effective_report_set())
    labels = [r[0] for r in bridge.rows]
    assert "− Finanzschulden" in labels and "Aktuell ausstehende Aktien" in labels
    lines = detail.source_lines(doc, doc.effective_report_set())
    ebit = next(t for t in lines if t.startswith("EBIT"))
    assert "Geschäftsjahr" in ebit and "Anbieter" in ebit


def test_calculation_path_shows_every_step_of_the_reference_case():
    doc = document()
    a = build(doc, margin="20").assumptions
    doc = detail.save_scenario(doc, a, NOW)
    calc = calculate_scenario(doc.effective_report_set(), a)
    path = detail.calculation_path(a, calc.result, calc.limitations)
    years = {r[0]: r for r in path.years.rows}
    assert years["= FCFF"][1:] == ("80,00", "88,00", "96,00", "104,00", "112,00")
    assert years["Barwert"][1] == "72,73"
    assert years["Diskontfaktor"][1] == "0,9091"
    bridge = dict(path.bridge.rows)
    assert bridge["= Wert des Eigenkapitals der Stammaktionäre"] == "1.276,50"
    terminal = dict(path.terminal.rows)
    assert terminal["Restwert am Ende von Jahr 5 = FCFF ÷ (WACC − g)"] == "1.632,00"
    assert path.terminal_share == "73,89 %"
    assert path.dominance_hint is None
    values = dict(path.value_lines.rows)
    assert values["Wert je Aktie = Eigenkapital ÷ Aktienzahl"] == "127,65 EUR"
    assert values["Zielpreis = Wert je Aktie × (1 − Abschlag)"] == "102,12 EUR"
    assert path.available


def test_terminal_dominance_is_visible_in_the_path_and_limits():
    doc = document()
    a = build(
        doc, wacc="8", growth="3", **{"t.capex": "69,44", "roic": "10"}
    ).assumptions
    doc = detail.save_scenario(doc, a, NOW)
    calc = calculate_scenario(doc.effective_report_set(), a)
    path = detail.calculation_path(a, calc.result, calc.limitations)
    assert path.dominance_hint and "75 %" in path.dominance_hint
    texts = detail.limitation_texts(calc.limitations)
    assert any("Terminalwertdominanz" in t for t in texts)
    assert any("kein statistisches Konfidenzintervall" in t for t in texts)


def test_blocked_result_has_no_value_and_explained_reasons():
    doc = document()
    a = build(doc, confirmed=False).assumptions
    doc = detail.save_scenario(doc, a, NOW)
    calc = calculate_scenario(doc.effective_report_set(), a)
    path = detail.calculation_path(a, calc.result, calc.limitations)
    assert not path.available
    values = dict(path.value_lines.rows)
    assert values["Wert je Aktie = Eigenkapital ÷ Aktienzahl"] == "n. v."
    texts = detail.blocking_texts(calc.result.issues)
    assert any("Annahmen noch nicht bestätigt" in t for t in texts)


def test_sensitivity_marks_the_centre_and_explains_invalid_combinations():
    doc = document()
    a = build(doc).assumptions
    doc = detail.save_scenario(doc, a, NOW)
    view = detail.sensitivity_view(doc.effective_report_set(), a, D("0.01"), D("0.005"))
    assert view.blocked == () and view.table is not None
    assert view.table.header[0] == "" and len(view.table.rows) == 5
    assert view.table.rows[2][3] == "127,65 ●"
    assert all(len(r) == 6 for r in view.table.rows)
    # WACC 8 % with g 3 % is valid; step 2 pp makes g >= WACC combinations appear.
    wide = detail.sensitivity_view(doc.effective_report_set(), a, D("0.02"), D("0.02"))
    cells = [c for r in wide.table.rows for c in r[1:]]
    assert any(c.startswith("n. v. (") for c in cells)
    assert any("g ≥ WACC" in n or "g < 0" in n for n in wide.notes)
    assert all(n.startswith("(") for n in wide.notes)


def test_sensitivity_of_a_blocked_scenario_gives_reasons_not_numbers():
    doc = document()
    a = build(doc, confirmed=False).assumptions
    doc = detail.save_scenario(doc, a, NOW)
    view = detail.sensitivity_view(doc.effective_report_set(), a, D("0.01"), D("0.005"))
    assert view.table is None
    assert any("Annahmen noch nicht bestätigt" in t for t in view.blocked)


def test_issue_explanations_are_german_and_keep_the_code():
    assert "Anzahl der Stammaktienklassen" in detail.explain_issue(
        QualityIssue(QualityStatus.INCOMPLETE, "ordinary_class_count_unknown")
    )
    text = detail.explain_issue(QualityIssue(QualityStatus.MISSING, "excess_cash"))
    assert "Überschüssige liquide Mittel" in text and "(excess_cash)" in text
    assert detail.explain_issue(QualityIssue(QualityStatus.INCOMPLETE, "xyz")).endswith(
        "(xyz)"
    )
    assert "ebit_definition" in detail.explain_issue(
        QualityIssue(QualityStatus.UNCONFIRMED_ASSUMPTIONS, "ebit_definition")
    )


def test_every_form_field_has_label_unit_and_explanation():
    for field_id in detail.FIELD_IDS:
        assert detail.field_label(field_id) and detail.field_help(field_id)
    assert detail.field_unit("y1.ebit", "EUR") == "Mio. EUR"
    assert detail.field_unit("wacc", "EUR") == "%"
    assert detail.field_unit("y2.tax", "EUR") == "%"
    assert detail.field_unit("rationale", "EUR") == ""


def test_model_version_and_new_document_are_unsaved_and_unassumed():
    doc = document()
    assert doc.revision == 0 and doc.model_version == MODEL_VERSION
    assert doc.scenarios == () and doc.manual_values == ()
    assert doc.data_version == doc.originals.input_version


def test_module_has_no_clock_ui_provider_or_network_dependency():
    from pathlib import Path

    source = Path(detail.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "datetime.now",
        "date.today",
        "time.time",
        "streamlit",
        "yfinance",
        "requests",
        "socket",
    ):
        assert forbidden not in source
