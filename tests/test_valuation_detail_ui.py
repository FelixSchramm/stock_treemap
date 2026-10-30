"""Detail view «Innerer Wert» (#80): AppTests with invented data, offline.

The demo entry point runs through ``app.py``; its scenario store is the real
``ScenarioRepository`` on a temporary database, so editing, saving, versions and
conflicts behave as in real operation. IP sockets are blocked.
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_valuation_ui import clean_environment, names, opened

import portfolio_dashboard.valuation_detail as vd
import portfolio_dashboard.valuation_detail_view as dv
import portfolio_dashboard.valuation_view as view
from portfolio_dashboard.fundamentals_demo import DEMO_VALUED_ID, demo_subjects
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.valuation_demo import DEMO_REPOSITORY_KEY

WACC = "valuation.edit.base.wacc"
GROWTH = "valuation.edit.base.growth"
CONFIRMED = "valuation.edit.base.confirmed"
SAVE = dv.SAVE_KEY


def identity(instrument_id=DEMO_VALUED_ID):
    return next(
        s.identity
        for s in demo_subjects().subjects
        if s.identity.listing.instrument.instrument_id == instrument_id
    )


def stored(app, instrument_id=DEMO_VALUED_ID):
    return app.session_state[DEMO_REPOSITORY_KEY].get(identity(instrument_id))


def open_detail(app=None, label="Demo-Aktie A"):
    app = app or opened()
    select = app.selectbox(key=view.DETAIL_SELECT_KEY)
    index = next(i for i, o in enumerate(select.options) if o.startswith(label))
    select.select_index(index)
    app.button(key=view.DETAIL_BUTTON_KEY).click().run()
    assert not app.exception
    return app


def metrics(app):
    return {m.label: m.value for m in app.metric}


def text(app):
    return " ".join(
        [m.value for m in app.markdown]
        + [c.value for c in app.caption]
        + [i.value for i in app.info]
        + [w.value for w in app.warning]
        + [e.value for e in app.error]
        + [s.value for s in app.success]
    )


def base_table(app, first_cell):
    return next(t.value for t in app.table if first_cell in t.value.index)


@pytest.fixture
def demo(monkeypatch):
    clean_environment(monkeypatch)
    monkeypatch.setenv("PORTFOLIO_WATCHLIST_RANGE", "Synthetic!A1:J400")


# --- opening and closing ------------------------------------------------------------------


def test_detail_opens_from_the_overview_and_consumes_the_requested_key(demo):
    app = opened()
    assert view.DETAIL_KEY not in app.session_state
    app.button(key=view.DETAIL_BUTTON_KEY).click().run()
    assert view.DETAIL_KEY not in app.session_state  # read and cleared
    assert app.session_state[view.DETAIL_OPEN_KEY] == DEMO_VALUED_ID
    assert [s.value for s in app.subheader] == [
        "Innerer Wert",
        "Details und Annahmen: Demo-Aktie A",
        "Historische Geschäftszahlen",
        "Manuelle Ergänzungen und Normalisierungen",
        "Zukunftsannahmen",
        "Rechenweg",
        "Sensitivität: WACC und Wachstum",
        "Modellgrenzen",
    ]
    assert not app.dataframe  # the overview is not drawn behind the detail view


def test_back_returns_to_the_overview_and_drops_unsaved_input(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    assert WACC in app.session_state
    app.button(key=dv.BACK_KEY).click().run()
    assert view.DETAIL_OPEN_KEY not in app.session_state
    assert WACC not in app.session_state
    assert app.dataframe and names(app)[0] == "Demo-Aktie A"
    app = open_detail(app)
    assert app.text_input(key=WACC).value == "10"  # back to the stored value


def test_demo_detail_says_that_everything_is_invented(demo):
    app = open_detail()
    assert "erfunden (synthetisch)" in text(app)
    assert "kein Kauf- oder Verkaufssignal" in text(app)
    assert "Konfidenzintervall" in text(app)


# --- reading: history, sources, calculation path, sensitivity ----------------------------


def test_history_sources_and_future_assumptions_are_separate_sections(demo):
    app = open_detail()
    history = base_table(app, "EBIT")
    assert list(history.columns) == [
        "GJ bis 31.12.2022",
        "GJ bis 31.12.2023",
        "GJ bis 31.12.2024",
        "GJ bis 31.12.2025",
    ]
    assert history.loc["EBIT"].tolist()[-1] == "150,00"
    assert history.loc["Steuersatz"].tolist()[0] == "20,00 % *"  # manual, marked
    labels = [e.label for e in app.expander]
    assert any(label.startswith("Quellen und Perioden aller Werte") for label in labels)
    sources = next(
        e for e in app.expander if e.label.startswith("Quellen und Perioden")
    )
    joined = " ".join(m.value for m in sources.markdown)
    assert "Geschäftsjahr 01.01.2025–31.12.2025" in joined
    assert "Anbieter yfinance, Zeile" in joined
    assert "manuell, Quelle:" in joined and "Original: fehlt" in joined
    subheaders = [s.value for s in app.subheader]
    assert subheaders.index("Historische Geschäftszahlen") < subheaders.index(
        "Zukunftsannahmen"
    )


def test_calculation_path_matches_the_documented_reference(demo):
    app = open_detail()
    assert metrics(app) == {
        "Wert je Aktie (Basis)": "135,15 EUR",
        "Zielpreis": "108,12 EUR",
        "Terminalwertanteil": "73,89 %",
    }
    years = base_table(app, "= FCFF")
    assert years.loc["= FCFF"].tolist() == [
        "80,00",
        "88,00",
        "96,00",
        "104,00",
        "112,00",
    ]
    assert years.loc["Barwert"].tolist()[0] == "72,73"
    bridge = base_table(app, "= Wert des Eigenkapitals der Stammaktionäre")
    assert (
        bridge.loc["= Wert des Eigenkapitals der Stammaktionäre"].iloc[0] == "1.351,50"
    )
    shown = text(app)
    assert "unverändert" in shown  # nothing edited: stored state
    assert "Terminalwertanteil" in shown


def test_other_scenarios_calculate_their_own_documented_values(demo):
    app = open_detail()
    for label, expected in (
        ("Vorsichtig", "106,98 EUR"),
        ("Optimistisch", "192,86 EUR"),
    ):
        app.radio(key=dv.SCENARIO_KEY).set_value(label).run()
        assert metrics(app)[f"Wert je Aktie ({label})"] == expected


def test_terminal_dominance_and_model_limits_are_visible(demo):
    app = open_detail()
    app.radio(key=dv.SCENARIO_KEY).set_value("Optimistisch").run()
    assert metrics(app)["Terminalwertanteil"] == "80,58 %"
    assert any("Terminalwertdominanz" in w.value for w in app.warning)
    shown = text(app)
    assert "keine sichere Prognose" in shown
    assert "kein Konfidenzintervall" in shown
    assert "Analystenziele" in shown


def test_sensitivity_is_compact_and_explains_invalid_combinations(demo):
    app = open_detail()
    grid = base_table(app, "WACC 10,00 %")
    assert grid.shape == (5, 5)
    assert grid.loc["WACC 10,00 %", "g 2,00 %"] == "135,15 ●"
    app.text_input(key=WACC).set_value("4").run()  # draft preview with a low WACC
    app.selectbox(key=dv.SENS_WACC_KEY).select_index(2)
    app.selectbox(key=dv.SENS_GROWTH_KEY).select_index(2).run()
    wide = base_table(app, "WACC 4,00 %")
    cells = [c for row in wide.values.tolist() for c in row]
    assert any(c.startswith("n. v. (") for c in cells)
    assert any("g ≥ WACC" in c.value or "g < 0" in c.value for c in app.markdown)


# --- editing: preview, save, versions ----------------------------------------------------


def test_changing_an_assumption_updates_the_preview_but_stores_nothing(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    assert not app.exception
    value = metrics(app)["Wert je Aktie (Basis)"]
    assert value != "135,15 EUR"
    assert "Nicht gespeicherte Änderungen" in text(app)
    assert "Vorschau (nicht gespeichert" in text(app)
    assert stored(app).revision == 1  # nothing was written
    grid = base_table(app, "WACC 9,00 %")
    assert grid.loc["WACC 9,00 %", "g 2,00 %"].startswith(value.split(" ")[0])


def test_explicit_save_creates_a_new_revision_and_reopening_keeps_it(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    new_value = metrics(app)["Wert je Aktie (Basis)"]
    app.button(key=SAVE).click().run()
    assert not app.exception and not app.error
    assert "Revision 2" in text(app)
    document = stored(app)
    assert document.revision == 2
    saved = document.scenario(Scenario.BASE).assumptions
    assert str(saved.wacc) == "0.09" and saved.version == "a2"
    assert saved.confirmed_at is not None and saved.inputs == document.inputs
    # The other two scenarios and the earlier revision are untouched.
    assert (
        document.scenario(Scenario.CAUTIOUS).assumptions.version
        == "demo-assumptions-v1"
    )
    first = app.session_state[DEMO_REPOSITORY_KEY].get(identity(), revision=1)
    assert first.scenario(Scenario.BASE).assumptions.wacc == Decimal("0.10")
    assert metrics(app)["Wert je Aktie (Basis)"] == new_value
    assert "unverändert" in text(app)
    # Back to the overview: the stored scenarios were reloaded after the save.
    app.button(key=dv.BACK_KEY).click().run()
    assert app.dataframe[0].value["Basis"].iloc[0] == new_value
    app = open_detail(app)
    assert app.text_input(key=WACC).value == "9"


def test_save_drops_the_loaded_state_and_reads_it_again(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    held = app.session_state[view.LOADED_STATE_KEY]
    app.button(key=SAVE).click().run()
    assert app.session_state[view.LOADED_STATE_KEY] is not held
    assert dv.RELOAD_KEY not in app.session_state  # consumed by the reload


def test_invalid_input_is_explained_at_the_field_and_nothing_is_stored(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("abc")
    app.text_input(key=GROWTH).set_value("10").run()
    shown = text(app)
    assert "Fehler:" in shown
    assert "Keine gültige Zahl" in shown
    assert "Die Vorschau erscheint, sobald alle Pflichtangaben gültig sind" in shown
    assert not app.metric  # no seemingly valid result
    app.button(key=SAVE).click().run()
    assert "Nicht gespeichert" in text(app)
    assert stored(app).revision == 1
    assert (
        stored(app).scenario(Scenario.BASE).assumptions
        == app.session_state[view.LOADED_STATE_KEY]
        .documents[DEMO_VALUED_ID]
        .scenario(Scenario.BASE)
        .assumptions
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("valuation.edit.base.wacc", "0", "größer als 0"),
        ("valuation.edit.base.growth", "12", "unter dem WACC"),
        ("valuation.edit.base.y1.tax", "100", "unter 100 %"),
        ("valuation.edit.base.y2.capex", "-3", "positiver Betrag"),
        ("valuation.edit.base.margin", "100", "unter 100 %"),
        ("valuation.edit.base.t.capex", "40", "Netto-Reinvestition"),
        ("valuation.edit.base.rationale", "", "Begründung"),
    ],
)
def test_each_invalid_field_gets_its_own_message(demo, field, value, message):
    app = open_detail()
    widget = (
        app.text_area(key=field)
        if field.endswith("rationale")
        else app.text_input(key=field)
    )
    widget.set_value(value).run()
    if not value:
        app.button(key=SAVE).click().run()
    assert message in text(app)
    assert not app.metric
    app.button(key=SAVE).click().run()
    assert stored(app).revision == 1


def test_unconfirmed_assumptions_save_but_give_no_value_in_the_overview(demo):
    app = open_detail()
    app.checkbox(key=CONFIRMED).set_value(False).run()
    assert metrics(app)["Wert je Aktie (Basis)"] == "135,15 EUR"  # preview arithmetic
    assert "Annahmen noch nicht bestätigt" in text(app)
    app.button(key=SAVE).click().run()
    assert stored(app).scenario(Scenario.BASE).assumptions.confirmed_at is None
    app.button(key=dv.BACK_KEY).click().run()
    row = app.dataframe[0].value.iloc[0]
    assert row["Basis"] == "n. v."
    assert row["Vorsichtig"] != "n. v."  # other scenarios stay independent


def test_unconfirmed_calculation_is_always_a_preview_never_a_saved_value(demo):
    app = open_detail()
    app.checkbox(key=CONFIRMED).set_value(False).run()
    assert "gespeichert kein Wert in der Übersicht" in text(app)
    app.button(key=SAVE).click().run()
    assert not app.exception and not app.error
    assert stored(app).scenario(Scenario.BASE).assumptions.confirmed_at is None
    shown = text(app)
    # Draft and stored state are identical, yet the arithmetic is only a preview.
    assert "Nicht gespeicherte Änderungen" not in shown
    assert "gespeicherter Stand, unverändert" not in shown
    assert "Vorschau (rechnerisch als bestätigt behandelt" in shown
    assert "gespeichert kein Wert in der Übersicht" in shown
    assert "Gespeichert (Revision 2): Wert je Aktie n. v." in shown


def test_the_scenario_choice_survives_a_save(demo):
    app = open_detail()
    app.radio(key=dv.SCENARIO_KEY).set_value("Vorsichtig").run()
    key = "valuation.edit.cautious.wacc"
    current = Decimal(app.text_input(key=key).value.replace(",", "."))
    app.text_input(key=key).set_value(str(current + 1)).run()
    app.button(key=SAVE).click().run()
    assert not app.exception and not app.error
    assert stored(app).revision == 2
    assert app.radio(key=dv.SCENARIO_KEY).value == "Vorsichtig"


def test_invalid_draft_still_shows_the_stored_states_model_limits(demo):
    app = open_detail()
    app.radio(key=dv.SCENARIO_KEY).set_value("Optimistisch").run()
    assert any("Terminalwertdominanz" in c.value for c in app.markdown)
    app.text_input(key="valuation.edit.optimistic.wacc").set_value("abc").run()
    assert not app.metric  # no preview for the invalid draft
    assert any("Terminalwertdominanz" in c.value for c in app.markdown)


def test_sensitivity_steps_do_not_trigger_the_widget_default_warning(demo, caplog):
    caplog.set_level("WARNING")
    app = open_detail()
    app.selectbox(key=dv.SENS_WACC_KEY).select_index(2).run()
    assert not app.exception
    assert app.selectbox(key=dv.SENS_WACC_KEY).index == 2
    assert not [r for r in caplog.records if "default value" in r.getMessage()]


def test_capex_helper_inserts_a_value_the_form_accepts(demo):
    app = open_detail()
    app.text_input(key="valuation.edit.base.y5.ebit").set_value("100")
    app.text_input(key="valuation.edit.base.y5.tax").set_value("20")
    app.text_input(key=GROWTH).set_value("2")
    app.text_input(key="valuation.edit.base.roic").set_value("13")
    app.text_input(key="valuation.edit.base.t.da").set_value("10")
    app.text_input(key="valuation.edit.base.t.dnwc").set_value("0").run()
    app.button(key=dv.COPY_CAPEX_KEY).click().run()
    assert not app.exception
    value = app.text_input(key="valuation.edit.base.t.capex").value
    assert len(value.split(",")[-1]) <= vd.MAX_FRACTION_DIGITS
    vd.parse_number(value)  # accepted by the form's own parser
    assert "Nachkommastellen" not in text(app)


def test_competing_editors_get_a_conflict_instead_of_a_lost_update(demo):
    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    # A second editor saves first.
    repo = app.session_state[DEMO_REPOSITORY_KEY]
    current = repo.get(identity())
    other = current.scenario(Scenario.CAUTIOUS).assumptions
    other = replace(other, wacc=other.wacc + Decimal("0.01"), version="other-1")
    theirs = repo.save(vd.save_scenario(current, other, current.changed_at))
    app.button(key=SAVE).click().run()
    assert "Daten wurden geändert" in text(app)
    assert stored(app).revision == theirs.revision  # nothing of ours was stored
    assert str(stored(app).scenario(Scenario.BASE).assumptions.wacc) == "0.10"
    assert app.text_input(key=WACC).value == "9"  # the input stays visible
    app.button(key="valuation.conflict_reload").click().run()
    assert app.text_input(key=WACC).value == "10"
    assert (
        app.session_state[view.LOADED_STATE_KEY].documents[DEMO_VALUED_ID].revision == 2
    )


# --- manual supplements and normalizations ---------------------------------------------


def pick(app, label_start):
    select = app.selectbox(key=dv.MANUAL_SELECT_KEY)
    index = next(i for i, o in enumerate(select.options) if label_start in o)
    select.select_index(index)
    app.run()


def reset_key(app, label_start):
    button = next(
        b
        for b in app.button
        if b.key.startswith(dv.RESET_KEY) and b.label.startswith(label_start)
    )
    return button.key


def test_manual_normalization_needs_reason_and_source_and_keeps_the_original(demo):
    app = open_detail()
    pick(app, "EBIT (operatives Ergebnis) · Geschäftsjahr 01.01.2025")
    key = f"{dv.MANUAL_PREFIX}ebit|Geschäftsjahr 01.01.2025–31.12.2025."
    app.text_input(key=key + "value").set_value("155").run()
    app.button(key=dv.MANUAL_SAVE_KEY).click().run()
    shown = text(app)
    assert "Bitte eine kurze Begründung angeben" in shown
    assert "Bitte einen Quellenhinweis angeben" in shown
    assert stored(app).revision == 1
    app.text_area(key=key + "reason").set_value("Einmalertrag bereinigt (erfunden)")
    app.text_area(key=key + "source").set_value("Beleg 7 (erfunden)")
    app.checkbox(key=key + "confirmed").check().run()
    app.button(key=dv.MANUAL_SAVE_KEY).click().run()
    assert not app.exception and not app.error
    document = stored(app)
    assert document.revision == 2
    manual = next(
        m for m in document.manual_values if m.adjustment.original.field.value == "ebit"
    )
    assert manual.adjustment.original.value == Decimal(150_000_000)  # original kept
    assert manual.adjustment.replacement.value == Decimal(155_000_000)
    assert manual.purpose.value == "normalization"
    shown = text(app)
    assert "Normalisierung" in shown and "statt 150,00 Mio. EUR" in shown
    assert "Annahmen müssen auf die neue Datenbasis geprüft werden" in shown
    # The stored plans keep their old binding: no silent transfer to the new data.
    assert "gehören zu einer früheren Datenbasis" in text(app)
    history = base_table(app, "EBIT")
    assert history.loc["EBIT"].tolist()[-1] == "155,00 *"
    app.button(key=dv.BACK_KEY).click().run()
    assert app.dataframe[0].value["Status"].iloc[0] != "Bewertet"


def test_reset_restores_the_automatic_source_and_marks_assumptions_for_review(demo):
    app = open_detail()
    key = reset_key(
        app, "Zurücksetzen: Operativer Steuersatz (Geschäftsjahr 01.01.2022"
    )
    app.button(key=key).click().run()
    assert not app.exception
    document = stored(app)
    assert document.revision == 2 and len(document.manual_values) == 8
    assert "Zurückgesetzt, die automatische Quelle gilt wieder" in text(app)
    history = base_table(app, "EBIT")
    assert history.loc["Steuersatz"].tolist()[0] == "fehlt"  # gap again, never zero
    assert history.loc["FCFF (abgeleitet)"].tolist()[0] == "n. v."
    assert "Angabe fehlt" in text(app) or "Pflichtangabe" in text(app)
    # Earlier revision keeps the supplement and its provenance.
    first = app.session_state[DEMO_REPOSITORY_KEY].get(identity(), revision=1)
    assert len(first.manual_values) == 9


def test_provider_suggestions_are_hints_and_never_adopted(demo):
    app = open_detail()
    key = reset_key(
        app, "Zurücksetzen: Operativer Steuersatz (Geschäftsjahr 01.01.2022"
    )
    app.button(key=key).click().run()
    pick(app, "Operativer Steuersatz · Geschäftsjahr 01.01.2022")
    shown = text(app)
    assert "Verwendungszweck: Ergänzung" in shown
    assert "Anbieter-Originalwert: fehlt" in shown
    key = f"{dv.MANUAL_PREFIX}operating_tax_rate|Geschäftsjahr 01.01.2022–31.12.2022."
    assert app.text_input(key=key + "value").value == ""  # nothing pre-filled


# --- a security without stored assumptions -----------------------------------------------


def test_security_without_assumptions_starts_empty_and_never_invents_values(demo):
    app = open_detail(label="Demo-Aktie US")
    shown = text(app)
    assert "noch nichts gespeichert" in shown
    assert "noch keine Annahmen gespeichert" in shown
    assert not app.metric
    assert app.text_input(key=WACC).value == ""
    assert "Die Vorschau erscheint, sobald alle Pflichtangaben gültig sind" in shown
    # The open data points are named, not hidden.
    assert "Offene Punkte der Datenbasis" in " ".join(e.label for e in app.expander)
    opened_points = next(e for e in app.expander if e.label.startswith("Offene Punkte"))
    points = " ".join(m.value for m in opened_points.markdown)
    assert "Operativer Steuersatz" in points
    assert "EBIT-Definition noch nicht bestätigt" in " ".join(
        m.value for m in opened_points.markdown
    )
    assert stored(app, "DEMO-FUNDAMENTALS-C") is None


def test_first_save_for_a_new_security_creates_revision_one_with_a_gap_state(demo):
    app = open_detail(label="Demo-Aktie US")
    prefix = "valuation.edit.base."
    for field_id, value in _base_values().items():
        key = prefix + field_id
        if field_id.endswith("rationale"):
            app.text_area(key=key).set_value(value)
        else:
            app.text_input(key=key).set_value(value)
    app.run()
    assert not app.exception
    app.checkbox(key=CONFIRMED).check().run()
    app.button(key=SAVE).click().run()
    assert not app.exception and not app.error
    document = stored(app, "DEMO-FUNDAMENTALS-C")
    assert document.revision == 1 and document.scenario(Scenario.BASE) is not None
    assert document.scenario(Scenario.CAUTIOUS) is None
    # Scenarios stay with their own security: the other demo stock is untouched.
    other = stored(app)
    assert other.revision == 1
    assert other.scenario(Scenario.BASE).assumptions.version == "demo-assumptions-v1"
    # Data gaps still block: a valid plan is not a finished valuation.
    app.button(key=dv.BACK_KEY).click().run()
    table = app.dataframe[0].value
    row = table[table["Name"] == "Demo-Aktie US"].iloc[0]
    assert row["Basis"] == "n. v." and row["Status"] != "Bewertet"


def _base_values():
    result = {}
    for n, ebit in enumerate((160, 170, 180, 190, 200), 1):
        result.update(
            {
                f"y{n}.ebit": str(ebit),
                f"y{n}.tax": "20",
                f"y{n}.da": "20",
                f"y{n}.capex": "48",
                f"y{n}.dnwc": "20",
            }
        )
    result.update(
        {
            "wacc": "10",
            "growth": "2",
            "roic": "10",
            "t.da": "20",
            "t.capex": "52,64",
            "t.dnwc": "0",
            "rationale": "erfunden",
            "t.rationale": "erfunden",
        }
    )
    return result


def test_unsupported_security_explains_why_there_is_no_editor(demo):
    app = open_detail(label="Demo-Aktie NYSE")
    assert any("keine verwertbaren Fundamentaldaten" in w.value for w in app.warning)
    assert not app.text_input


# --- privacy, keyboard, mobile ------------------------------------------------------------


def test_private_texts_never_reach_logs_or_output(demo, caplog, capsys):
    app = open_detail()
    pick(app, "EBIT (operatives Ergebnis) · Geschäftsjahr 01.01.2025")
    key = f"{dv.MANUAL_PREFIX}ebit|Geschäftsjahr 01.01.2025–31.12.2025."
    app.text_input(key=key + "value").set_value("155")
    app.text_area(key=key + "reason").set_value("geheimer-grund-4711")
    app.text_area(key=key + "source").set_value("geheimer-beleg-4711")
    app.checkbox(key=key + "confirmed").check().run()
    with caplog.at_level("DEBUG"):
        app.button(key=dv.MANUAL_SAVE_KEY).click().run()
    out = capsys.readouterr()
    for channel in (caplog.text, out.out, out.err):
        assert "4711" not in channel


def test_editor_uses_only_native_labelled_keyboard_widgets_with_units(demo):
    app = open_detail()
    assert not app.get("plotly_chart") and not app.get("html")
    labels = [w.label for w in app.text_input]
    assert "WACC (Kapitalkostensatz) (%)" in labels
    assert "Jahr 1: EBIT (Mio. EUR)" in labels
    assert "Jahr 3: Operativer Steuersatz (%)" in labels
    assert "Folgejahr: Investitionen (Capex) (Mio. EUR)" in labels
    assert all(label for label in labels)
    expanders = " ".join(e.label for e in app.expander)
    assert "Felder und Einheiten erklärt" in expanders
    explained = next(
        e for e in app.expander if e.label == "Felder und Einheiten erklärt"
    )
    body = " ".join(m.value for m in explained.markdown)
    assert "Operativer Steuersatz" in body and "ROIC" in body and "FCFF" in body
    assert app.date_input and app.date_input[0].label.startswith(
        "Das erste Prognosejahr"
    )
    assert all(not w.disabled for w in app.button if w.key == SAVE)


def test_ui_text_has_no_buy_sell_or_analyst_target_wording(demo):
    shown = text(open_detail()).lower()
    for forbidden in (
        "kaufen",
        "verkaufen",
        "kaufempfehlung",
        "unterbewertet",
        "kursziel",
    ):
        assert forbidden not in shown


# --- wiring: the protected store, owner/loopback rules ------------------------------------------


def test_production_wiring_saves_through_the_protected_store_only_on_loopback(
    monkeypatch, tmp_path
):
    import streamlit as st
    from test_valuation_ui import LOCAL

    from portfolio_dashboard.fundamentals_demo import (
        demo_fundamentals_loader,
        demo_registry,
    )
    from portfolio_dashboard.private_store import PrivateStorageError

    clean_environment(monkeypatch)
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PORTFOLIO_PRIVATE_DATA_DIR", str(tmp_path / "private"))
    address = {"value": "127.0.0.1"}
    original = st.get_option
    monkeypatch.setattr(
        st,
        "get_option",
        lambda key: address["value"] if key == "server.address" else original(key),
    )
    loader = demo_fundamentals_loader()
    instrument = next(
        listing.instrument
        for listing in demo_registry()
        if listing.instrument.instrument_id == DEMO_VALUED_ID
    )
    entry = loader.load([instrument]).entry(instrument)
    source = view.private_source(demo_registry())
    assert source.scenario_saver is not None and source.demo is False

    document = vd.new_document(entry.report_set, source.clock())
    saved = source.scenario_saver(document)
    assert saved.revision == 1
    assert source.scenario_reader(entry.identity) == saved
    assert (tmp_path / "private" / "private.sqlite3").exists()

    # A stale editor (revision 0 again) gets the store's conflict, nothing is overwritten.
    from portfolio_dashboard.scenarios import ScenarioConflict

    with pytest.raises(ScenarioConflict):
        source.scenario_saver(document)

    # Not loopback: neither read nor write reaches the protected store.
    address["value"] = "0.0.0.0"
    for call in (
        lambda: source.scenario_saver(saved),
        lambda: source.scenario_reader(entry.identity),
    ):
        with pytest.raises(PrivateStorageError) as error:
            call()
        assert error.value.code == "local"


def test_saving_a_storage_failure_shows_the_fixed_message_and_keeps_the_input(demo):
    from portfolio_dashboard.private_store import PrivateStorageError

    app = open_detail()
    app.text_input(key=WACC).set_value("9").run()
    repository = app.session_state[DEMO_REPOSITORY_KEY]

    def failing(document):
        raise PrivateStorageError("busy")

    repository.save = failing  # the next write fails like a locked database
    app.button(key=SAVE).click().run()
    assert not app.exception
    assert any("Private Datenbank wird gerade verwendet" in e.value for e in app.error)
    assert "Nicht gespeichert" in " ".join(e.value for e in app.error)
    assert app.text_input(key=WACC).value == "9"


# --- a new report ---------------------------------------------------------------------------


def with_new_report(app):
    """Make the loader deliver a newer report set than the stored scenario document."""
    held = app.session_state[view.LOADED_STATE_KEY]
    instrument = next(
        i for i in held.bundle.entries if i.instrument_id == DEMO_VALUED_ID
    )
    entry = held.bundle.entries[instrument]
    newer = replace(entry.report_set, input_version="newer-report")
    entries = {
        i: (
            replace(e, report_set=newer)
            if e.report_set is not None and i.instrument_id == DEMO_VALUED_ID
            else e
        )
        for i, e in held.bundle.entries.items()
    }
    app.session_state[view.LOADED_STATE_KEY] = replace(
        held, bundle=replace(held.bundle, entries=entries)
    )
    return newer


def test_a_new_report_blocks_editing_until_it_is_adopted_and_then_flags_everything(
    demo,
):
    app = open_detail()
    newer = with_new_report(app)
    app.run()
    assert not app.exception
    shown = text(app)
    assert (
        "Neuer Bericht" in shown
        and "werden nicht auf die neuen Zahlen angewendet" in shown
    )
    assert not app.text_input and not app.text_area  # no editor on stale data
    assert [b.label for b in app.button if b.key == dv.ADOPT_KEY] == [
        "Neuen Berichtssatz übernehmen"
    ]
    assert stored(app).revision == 1  # nothing changed without the explicit action
    app.button(key=dv.ADOPT_KEY).click().run()
    assert not app.exception
    document = stored(app)
    assert document.revision == 2
    assert document.originals.input_version == "newer-report"
    # The prompt from #76: manual values and plans are flagged, nothing moved silently.
    assert len(vd.review_needed(document)) == 9
    codes = {i.code for i in document.issues_for(Scenario.BASE) if i.blocking}
    assert {"assumptions_input_mismatch", "manual_review_required"} <= codes
    assert document.scenario(Scenario.BASE).assumptions.inputs != document.inputs
    first = app.session_state[DEMO_REPOSITORY_KEY].get(identity(), revision=1)
    assert first.originals.input_version != "newer-report"  # history kept
    assert newer.input_version == "newer-report"
