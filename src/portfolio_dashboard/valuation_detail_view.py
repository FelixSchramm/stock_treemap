"""Streamlit detail view of one security «Innerer Wert» (M8-11, #80).

History with source and period, the owner's manual supplements and normalizations,
the editor for the three scenarios with a live preview, the calculation path, the
WACC/growth sensitivity and the model limits. All arithmetic comes unchanged from
``dcf``; parsing, validation and the display tables come from the pure
``valuation_detail``. Nothing is stored without an explicit save; a save goes through
the scenario store of #76 (``OverviewSource.scenario_saver``) with its owner check,
versioning and conflict detection. No buy/sell signal, no default financial value.

Unsaved input lives in widget state under the prefixes below. It is retained while
other scenarios or sections are edited and dropped when the view is left, after a
save and when another revision is loaded.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

from portfolio_dashboard import valuation_detail as vd
from portfolio_dashboard.fundamentals import FundamentalField
from portfolio_dashboard.fundamentals_loader import FundamentalsEntry
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.private_store import PrivateStorageError
from portfolio_dashboard.scenarios import ScenarioConflict, ScenarioDocument
from portfolio_dashboard.valuation_detail import markdown_text
from portfolio_dashboard.valuation_overview import OverviewRow, OverviewSource

EDIT_PREFIX = "valuation.edit."
MANUAL_PREFIX = "valuation.manual."
SENS_PREFIX = "valuation.sens."
DRAFT_PREFIXES = (EDIT_PREFIX, MANUAL_PREFIX, SENS_PREFIX)
SEED_KEY = "valuation.draft_seed"
NOTICE_KEY = "valuation.notice"
CONFLICT_KEY = "valuation.conflict"
BACK_KEY = "valuation.detail_back"
SCENARIO_KEY = EDIT_PREFIX + "scenario"
SAVE_KEY = EDIT_PREFIX + "save"
COPY_CAPEX_KEY = EDIT_PREFIX + "copy_capex"
ADOPT_KEY = "valuation.adopt_report"
RELOAD_KEY = "valuation.reload_after_save"
MANUAL_SELECT_KEY = MANUAL_PREFIX + "select"
MANUAL_SAVE_KEY = MANUAL_PREFIX + "save"
SENS_WACC_KEY = SENS_PREFIX + "wacc_step"
SENS_GROWTH_KEY = SENS_PREFIX + "growth_step"

INTRO_TEXT = (
    "Modellschätzung aus einem FCFF-DCF mit Ihren eigenen Annahmen: kein Kauf- oder "
    "Verkaufssignal, kein Konfidenzintervall, keine Prognose. Beträge stehen in Millionen "
    "der Berichtswährung (Mio.), Raten in Prozent, der Wert je Aktie in der Berichtswährung. "
    "Fehlende Werte erscheinen als „fehlt“ oder „n. v.“, nie als 0."
)
DEMO_TEXT = (
    "Demo: Alle Annahmen, manuellen Werte und Zahlen sind erfunden (synthetisch). Gespeichert "
    "wird nur in einem temporären Speicher dieser Sitzung."
)
LEAVE_TEXT = "Nicht gespeicherte Eingaben gehen beim Verlassen der Detailansicht verloren."
OWNER_CONFIRMATIONS = (
    ("ebit_definition", "EBIT-Definition", "confirm_ebit_definition"),
    ("share_split_basis", "Splitbasis der Aktienzahl", "confirm_share_split_basis"),
    ("lease_treatment", "Leasing-Behandlung", "confirm_lease_treatment"),
)
CONFIRMATIONS_TEXT = (
    "Diese drei Bestätigungen und die Zahl der Stammaktienklassen liegen in der privaten "
    "Fundamentaldaten-Zuordnung (Datei bzw. Secrets), nicht in der App. Hier werden sie nur "
    "angezeigt; geändert werden sie dort."
)
STEP_WACC_LABEL = "Schrittweite WACC"
STEP_GROWTH_LABEL = "Schrittweite Wachstum g"


@dataclass(frozen=True, slots=True, repr=False)
class DetailContext:
    """What the detail view needs from the overview run."""

    source: OverviewSource
    row: OverviewRow
    entry: FundamentalsEntry | None
    document: ScenarioDocument | None
    now: datetime
    on_saved: Callable[[], None]
    on_close: Callable[[], None]


# --- state ---------------------------------------------------------------------------


def clear_drafts() -> None:
    """Drop every unsaved input of the detail view (leaving, saving, reloading)."""
    for key in list(st.session_state):
        if isinstance(key, str) and key.startswith(DRAFT_PREFIXES):
            del st.session_state[key]
    for key in (SEED_KEY, CONFLICT_KEY):
        st.session_state.pop(key, None)


def _retain_drafts() -> None:
    """Streamlit drops widgets that are not drawn; keep the other scenarios' input."""
    for key in list(st.session_state):
        if isinstance(key, str) and key.startswith(DRAFT_PREFIXES):
            st.session_state[key] = st.session_state[key]


def _seed(doc: ScenarioDocument) -> None:
    """New drafts whenever the stored basis changes (another revision, report, data)."""
    seed = (doc.inputs.instrument_id, doc.revision, doc.data_version, doc.originals.input_version)
    if st.session_state.get(SEED_KEY) != seed:
        for key in list(st.session_state):
            if isinstance(key, str) and key.startswith(DRAFT_PREFIXES):
                del st.session_state[key]
        st.session_state[SEED_KEY] = seed


def _key(scenario: Scenario, name: str) -> str:
    return f"{EDIT_PREFIX}{scenario.value}.{name}"


def _attempted_key(scenario: Scenario) -> str:
    return _key(scenario, "attempted")


def _mark_attempted(scenario: Scenario) -> None:
    st.session_state[_attempted_key(scenario)] = True


def _seed_scenario(scenario: Scenario, doc: ScenarioDocument, now: datetime) -> None:
    today = now.astimezone(timezone.utc).date()
    base = vd.form_from_saved(doc.scenario(scenario), doc, today)
    for field_id in vd.FIELD_IDS:
        st.session_state.setdefault(_key(scenario, field_id), base.text(field_id))
    st.session_state.setdefault(_key(scenario, "first_year_end"), base.first_year_end)
    st.session_state.setdefault(_key(scenario, "confirmed"), base.confirmed)


def _form(scenario: Scenario) -> vd.ScenarioForm:
    return vd.ScenarioForm(
        {f: st.session_state.get(_key(scenario, f), "") for f in vd.FIELD_IDS},
        st.session_state[_key(scenario, "first_year_end")],
        bool(st.session_state.get(_key(scenario, "confirmed"), False)),
    )


# --- small drawing helpers -----------------------------------------------------------


def _table(data: vd.TableData) -> None:
    """Real HTML table (readable by assistive technology), first column as row label."""
    frame = pd.DataFrame(list(data.rows), columns=list(data.header))
    st.table(frame.set_index(data.header[0]))


def _error(message: str | None, text: str | None, attempted: bool) -> None:
    """Field-near explanation: shown once something is typed or a save was tried."""
    if message and (attempted or (text or "").strip()):
        st.markdown(f":red[**Fehler:** {markdown_text(message)}]")


def _lines(lines) -> None:
    st.markdown("\n".join(f"- {markdown_text(line)}" for line in lines))


def _commit(
    ctx: DetailContext, document: ScenarioDocument, success: str
) -> None:
    """Store a new revision through the scenario store; never a partial result."""
    saver = ctx.source.scenario_saver
    if saver is None:
        st.error("Speichern ist in dieser Konfiguration nicht eingerichtet.")
        return
    try:
        saved = saver(document)
    except ScenarioConflict as exc:
        st.session_state[CONFLICT_KEY] = str(exc)
        st.rerun()
        return
    except PrivateStorageError as exc:
        st.error(f"Nicht gespeichert. {exc}")
        return
    except (ValueError, TypeError):
        st.error(f"Nicht gespeichert. {vd.GENERIC_ERROR}")
        return
    clear_drafts()
    st.session_state[NOTICE_KEY] = f"{success} (Revision {saved.revision})."
    st.session_state[RELOAD_KEY] = True
    ctx.on_saved()
    st.rerun()


# --- sections ------------------------------------------------------------------------


def _render_summary(ctx: DetailContext) -> None:
    row = ctx.row
    st.markdown(
        f"**Status in der Übersicht:** {markdown_text(row.status_text)} · "
        f"**Kurs:** {markdown_text(row.price_label)}"
        + (f" ({markdown_text(row.price_source)})" if row.price_source else "")
    )
    st.caption(
        f"Kursvergleich (Basis, gespeicherter Stand): {row.comparison_text}"
        + (f" – {row.position_text}" if row.position_text else "")
    )
    st.caption(
        f"Persönlicher Zielpreis (Basis, gespeichert): {row.target_label} · "
        f"Sicherheitsabschlag: {row.margin_label}"
    )
    with st.expander("Datenstand und Hinweise", expanded=False):
        _lines(row.stand_lines)
        if row.notes:
            _lines(f"Hinweis: {n}" for n in row.notes)


def _render_open_points(doc: ScenarioDocument, now: datetime) -> None:
    effective = doc.effective_report_set()
    issues = effective.issues_at(now)
    blocking = vd.blocking_texts(issues)
    with st.expander(f"Offene Punkte der Datenbasis ({len(blocking)})", expanded=bool(blocking)):
        if blocking:
            st.caption(
                "Solange diese Punkte offen sind, liefert kein Szenario einen Wert. Manche lassen "
                "sich unten durch eine belegte Ergänzung schließen, andere (Bestätigungen, "
                "Klassenanzahl) nur in der privaten Zuordnung."
            )
            _lines(blocking)
        else:
            st.caption("Die Datenbasis ist vollständig und bestätigt.")
        notes = vd.advisory_texts(issues)
        if notes:
            st.caption("Hinweise ohne Sperre:")
            _lines(notes)
        codes = {i.code for i in effective.qualifications}
        st.markdown("**Bestätigungen aus der privaten Konfiguration (nur Anzeige)**")
        _lines(
            f"{label}: {'ausstehend' if code in codes else 'bestätigt'} ({option})"
            for code, label, option in OWNER_CONFIRMATIONS
        )
        classes = [i for i in effective.capital_structure.issues]
        _lines(
            [
                "Zahl der Stammaktienklassen: "
                + (vd.explain_issue(classes[0]) if classes else "genau eine, belegt")
            ]
        )
        st.caption(CONFIRMATIONS_TEXT)


def _render_history(doc: ScenarioDocument) -> None:
    st.subheader("Historische Geschäftszahlen")
    effective = doc.effective_report_set()
    currency = effective.currency
    st.caption(
        f"Anbieterwerte je Geschäftsjahr in Mio. {currency}; ein Stern (*) kennzeichnet einen "
        "bestätigten manuellen Wert, „fehlt“ eine Lücke. ΔNWC und FCFF sind aus den Angaben "
        "abgeleitet (EBIT × (1 − Steuersatz) + D&A − Capex − ΔNWC); die Rechnung selbst steht im Rechenweg."
    )
    _table(vd.history_table(effective))
    st.caption("Kapitalbrücke und Aktienzahl (Mio. " + currency + ", Aktien in Mio. Stück):")
    _table(vd.bridge_table(effective))
    sources = vd.source_lines(doc, effective)
    with st.expander(f"Quellen und Perioden aller Werte ({len(sources)})", expanded=False):
        st.caption(
            "Je Wert: Periode, Quelle und Abruf bzw. die Herkunft der manuellen Ergänzung. "
            "Veröffentlichungsdaten sind beim Anbieter unbekannt und werden nicht aus dem Abruf geschätzt."
        )
        _lines(sources)


def _suggestions(ctx: DetailContext, item: vd.ManualItem) -> list[str]:
    entry = ctx.entry
    if entry is None or entry.normalized is None:
        return []
    currency = item.original.report and (ctx.document or None) and ""
    _ = currency
    result = []
    for s in entry.normalized.suggestions:
        if s.field is item.original.field and s.period in (None, item.original.period):
            kind = vd.unit_kind(s.field)
            shown = (
                vd.format_percent(s.value)
                if kind.value == "rate"
                else vd.format_money(s.value)
            )
            result.append(f"Anbieter-Vorschlag (nicht übernommen): {shown} – {s.basis}")
    return result


def _item_option(item: vd.ManualItem, currency: str) -> str:
    if item.saved is not None:
        state = "manuell gesetzt"
    elif item.original.status.value != "available":
        state = "fehlt"
    else:
        state = vd.format_observation(item.original, currency)
    return f"{item.label} – {state}"


def _render_manual(ctx: DetailContext, doc: ScenarioDocument, editable: bool) -> None:
    st.subheader("Manuelle Ergänzungen und Normalisierungen")
    currency = doc.originals.currency
    st.caption(
        "Fehlt eine Pflichtangabe oder soll ein Anbieterwert normalisiert werden, tragen Sie hier "
        "einen belegten Wert ein. Ein bestätigter manueller Wert hat sichtbar Vorrang vor der "
        "automatischen Quelle; der Anbieter-Originalwert bleibt erhalten und gilt wieder, sobald "
        "Sie zurücksetzen. Eine Ergänzung wird nie durch ein Aktualisieren überschrieben. "
        "Begründung und Quellenhinweis sind Pflicht; der Quellenhinweis bleibt in der privaten Ablage."
    )
    for index, (manual, text) in enumerate(vd.manual_lines(doc)):
        st.markdown(f"- {markdown_text(text)}")
        if editable and st.button(
            f"Zurücksetzen: {vd.FIELD_NAMES[manual.adjustment.original.field]} "
            f"({vd.period_label(manual.adjustment.original.period)})",
            key=f"{MANUAL_PREFIX}reset.{index}",
        ):
            _commit(
                ctx,
                vd.reset_manual(doc, manual, ctx.now),
                "Zurückgesetzt, die automatische Quelle gilt wieder",
            )
    items = vd.manual_items(doc)
    if not items:
        st.caption("Es gibt keine Werte, die ergänzt werden könnten.")
        return
    if not editable:
        st.caption("Ergänzen ist erst nach Übernahme des neuen Berichtssatzes möglich.")
        return
    options = range(len(items))
    chosen = st.selectbox(
        "Wert ergänzen oder normalisieren",
        options,
        format_func=lambda i: _item_option(items[i], currency),
        key=MANUAL_SELECT_KEY,
    )
    item = items[chosen]
    field_ = item.original.field
    st.markdown(
        f"**{markdown_text(FIELD_LABEL(field_))}** · {markdown_text(vd.period_label(item.original.period))}"
    )
    st.caption(vd.FIELD_HELP.get(field_, ""))
    st.caption(
        f"Einheit der Eingabe: {vd.unit_hint(field_, currency)}. "
        f"Anbieter-Originalwert: {vd.format_observation(item.original, currency)}."
        + (
            " Verwendungszweck: Ergänzung (der Anbieter liefert keinen Wert)."
            if item.purpose.value == "supplement"
            else " Verwendungszweck: Normalisierung (weicht vom gelieferten Wert ab)."
        )
    )
    for hint in _suggestions(ctx, item):
        st.caption(hint)
    prefix = f"{MANUAL_PREFIX}{item.key}."
    unit = vd.unit_hint(field_, currency)
    value = st.text_input(f"Wert ({unit})", key=prefix + "value")
    reason = st.text_area(
        "Begründung (Pflicht)", key=prefix + "reason", max_chars=vd.MAX_TEXT, height=80
    )
    source = st.text_area(
        "Quellenhinweis (Pflicht, bleibt privat)",
        key=prefix + "source",
        max_chars=vd.MAX_TEXT,
        height=80,
    )
    confirmed = st.checkbox(
        "Ich bestätige den Wert anhand des Belegs (ohne Bestätigung bleibt er wirkungslos)",
        key=prefix + "confirmed",
    )
    approximation = st.checkbox(
        "Der Wert ist eine Buchwertnäherung für den Marktwert", key=prefix + "approx"
    )
    manual, errors = vd.build_manual(
        doc,
        item,
        vd.ManualForm(value, reason, source, confirmed, approximation),
        now=ctx.now,
    )
    attempted_key = prefix + "attempted"
    for name, text in (("value", value), ("reason", reason), ("source", source)):
        _error(errors.get(name), text, bool(st.session_state.get(attempted_key)))
    if vd.FORM_ERROR_KEY in errors:
        st.markdown(f":red[**Fehler:** {markdown_text(errors[vd.FORM_ERROR_KEY])}]")
    st.caption("Priorität: Ein bestätigter manueller Wert gilt vor der automatischen Quelle.")
    if st.button(
        "Ergänzung speichern",
        key=MANUAL_SAVE_KEY,
        on_click=lambda: st.session_state.__setitem__(attempted_key, True),
    ):
        if manual is None:
            st.error("Nicht gespeichert: Bitte die markierten Felder korrigieren.")
        else:
            _commit(
                ctx,
                vd.add_manual(doc, manual, ctx.now),
                "Ergänzung gespeichert; Annahmen müssen auf die neue Datenbasis geprüft werden",
            )


def FIELD_LABEL(field_: FundamentalField) -> str:  # noqa: N802 - readable constant-like helper
    return vd.FIELD_NAMES[field_]


def _render_field_help(currency: str) -> None:
    with st.expander("Felder und Einheiten erklärt", expanded=False):
        st.caption(
            f"Beträge in Mio. {currency} (Dezimalkomma oder -punkt, ohne Tausendertrennzeichen), "
            "Raten in Prozent. Die Hilfe steht auch als Tooltip am Feld, hier ohne Hover."
        )
        lines = []
        for name in vd.YEAR_FIELD_LABELS:
            unit = "%" if name == "tax" else f"Mio. {currency}"
            lines.append(
                f"**{vd.YEAR_FIELD_LABELS[name]}** je Prognosejahr ({unit}): {vd.YEAR_FIELD_HELP[name]}"
            )
        for field_id in ("wacc", "growth", "roic", "margin", "t.da", "t.capex", "t.dnwc", "rationale", "t.rationale"):
            unit = vd.field_unit(field_id, currency)
            lines.append(
                f"**{vd.SCALAR_LABELS[field_id]}**" + (f" ({unit})" if unit else "") + f": {vd.SCALAR_HELP[field_id]}"
            )
        lines.append(
            "**FCFF** je Jahr = EBIT × (1 − Steuersatz) + D&A − Capex − ΔNWC. Der Restwert entsteht aus dem "
            "nachhaltigen Folgejahr (Jahr 6): NOPAT = NOPAT Jahr 5 × (1 + g) (wird abgeleitet), "
            "Netto-Reinvestition = NOPAT × g ÷ ROIC."
        )
        st.markdown("\n\n".join(markdown_text_keep_bold(x) for x in lines))


def markdown_text_keep_bold(text: str) -> str:
    """Escape the text but keep the ** emphasis pairs written by this module."""
    parts = text.split("**")
    return "**".join(markdown_text(p) for p in parts)


def _field(
    scenario: Scenario,
    field_id: str,
    currency: str,
    errors: Mapping[str, str],
    attempted: bool,
) -> None:
    unit = vd.field_unit(field_id, currency)
    label = vd.field_label(field_id) + (f" ({unit})" if unit else "")
    key = _key(scenario, field_id)
    if field_id in vd.TEXT_FIELDS:
        st.text_area(
            label, key=key, help=vd.field_help(field_id), max_chars=vd.MAX_TEXT, height=80
        )
    else:
        st.text_input(label, key=key, help=vd.field_help(field_id))
    _error(errors.get(field_id), st.session_state.get(key), attempted)


def _copy_capex(scenario: Scenario) -> None:
    help_ = vd.terminal_help(_form(scenario))
    if help_ is not None and help_.required_capex is not None and help_.required_capex >= 0:
        st.session_state[_key(scenario, "t.capex")] = vd.money_input(help_.required_capex)


def _render_editor(ctx: DetailContext, doc: ScenarioDocument, editable: bool):
    """The scenario editor; returns (scenario, built assumptions or None, show_saved)."""
    st.subheader("Zukunftsannahmen")
    currency = doc.originals.currency
    st.caption(
        "Ab hier stehen Ihre eigenen Annahmen, getrennt von den Geschäftszahlen oben. Es gibt keine "
        "Vorgabewerte: Ohne vollständige, gültige und bestätigte Annahmen entsteht kein Wert. "
        "Annahmen gelten für genau diese Datenbasis; nach einer geänderten Datenbasis oder einem "
        "neuen Bericht müssen sie hier geprüft und erneut gespeichert werden. " + LEAVE_TEXT
    )
    _render_field_help(currency)
    labels = {s: vd.SCENARIO_LABELS[s] for s in vd.SCENARIO_ORDER}
    st.session_state.setdefault(SCENARIO_KEY, labels[Scenario.BASE])
    chosen = st.radio(
        "Szenario", list(labels.values()), key=SCENARIO_KEY, horizontal=True
    )
    scenario = next(s for s, label in labels.items() if label == chosen)
    saved = doc.scenario(scenario)
    if saved is None:
        st.info(f"Für „{chosen}“ sind noch keine Annahmen gespeichert.")
    elif saved.assumptions.inputs != doc.inputs:
        st.warning(
            "Die gespeicherten Annahmen gehören zu einer früheren Datenbasis und liefern keinen Wert, "
            "bis sie hier geprüft und erneut gespeichert werden. Die Felder zeigen die früheren Zahlen "
            "als Ausgangspunkt; die Bestätigung muss neu gegeben werden."
        )
    _seed_scenario(scenario, doc, ctx.now)
    attempted = bool(st.session_state.get(_attempted_key(scenario)))
    version = vd.next_assumptions_version(doc)
    built = vd.build_assumptions(
        _form(scenario), scenario, doc, now=ctx.now, version=version
    )
    errors = built.errors

    st.markdown("**Prognosezeitraum**")
    st.date_input(
        "Das erste Prognosejahr endet am",
        key=_key(scenario, "first_year_end"),
        format="DD.MM.YYYY",
        min_value=datetime(2000, 1, 1).date(),
        max_value=datetime(2100, 12, 31).date(),
        help="Vier weitere Jahre folgen jeweils zwölf Monate später. Das erste Jahr muss nach dem Bewertungsstichtag beginnen.",
    )
    if vd.FIRST_YEAR_ERROR_KEY in errors:
        st.markdown(f":red[**Fehler:** {markdown_text(errors[vd.FIRST_YEAR_ERROR_KEY])}]")
    last = max(
        (p.end for p in vd.annual_periods(doc.originals)), default=None
    )
    st.caption(
        f"Bewertungsstichtag (t = 0): {vd.format_date(ctx.now)}"
        + (f" · letzter berichteter Jahresabschluss: {vd.format_date(last)}" if last else "")
        + ". Die Cashflows fallen jeweils am Jahresende an."
    )
    for n in range(1, vd.YEARS + 1):
        with st.container(border=True):
            end = vd.add_years(st.session_state[_key(scenario, "first_year_end")], n - 1)
            st.markdown(f"**Prognosejahr {n}** (Geschäftsjahr bis {vd.format_date(end)})")
            columns = st.columns(2)
            for position, name in enumerate(vd.YEAR_FIELD_LABELS):
                with columns[position % 2]:
                    _field(scenario, f"y{n}.{name}", currency, errors, attempted)
    with st.container(border=True):
        st.markdown("**Zinssätze und Wachstum**")
        columns = st.columns(2)
        for position, field_id in enumerate(("wacc", "growth", "roic", "margin")):
            with columns[position % 2]:
                _field(scenario, field_id, currency, errors, attempted)
    with st.container(border=True):
        st.markdown("**Nachhaltiges Folgejahr (Jahr 6) für den Restwert**")
        help_ = built.terminal
        if help_ is not None:
            st.caption(
                f"Abgeleitet: NOPAT Jahr 5 = {vd.format_money(help_.nopat_5)} Mio. {currency}; "
                f"NOPAT Folgejahr = NOPAT Jahr 5 × (1 + g) = {vd.format_money(help_.nopat_6)} Mio. {currency}; "
                f"nötige Netto-Reinvestition = NOPAT × g ÷ ROIC = {vd.format_money(help_.net_reinvestment, 4)} "
                f"Mio. {currency}, also Capex + ΔNWC − D&A."
            )
        else:
            st.caption(
                "Sobald EBIT und Steuersatz von Jahr 5 sowie g und ROIC gültig eingetragen sind, "
                "erscheinen hier NOPAT des Folgejahres und die nötige Netto-Reinvestition."
            )
        columns = st.columns(2)
        for position, field_id in enumerate(("t.da", "t.capex", "t.dnwc")):
            with columns[position % 2]:
                _field(scenario, field_id, currency, errors, attempted)
        if help_ is not None and help_.required_capex is not None and help_.required_capex >= 0:
            st.caption(
                f"Mit den eingetragenen D&A und ΔNWC passt Capex = {vd.format_money(help_.required_capex, 4)} Mio. {currency}."
            )
            st.button(
                "Capex des Folgejahres entsprechend eintragen",
                key=COPY_CAPEX_KEY,
                on_click=_copy_capex,
                args=(scenario,),
            )
    _field(scenario, "rationale", currency, errors, attempted)
    _field(scenario, "t.rationale", currency, errors, attempted)
    st.checkbox(
        "Ich bestätige diese Annahmen als meine eigenen und habe sie geprüft "
        "(ohne Bestätigung liefert das gespeicherte Szenario keinen Wert)",
        key=_key(scenario, "confirmed"),
    )
    if vd.FORM_ERROR_KEY in errors:
        st.markdown(f":red[**Fehler:** {markdown_text(errors[vd.FORM_ERROR_KEY])}]")

    # Status of the draft against the stored scenario.
    if built.assumptions is None:
        open_fields = [vd.field_label(f) for f in errors if f in vd.FIELD_IDS]
        st.warning(
            "Die Vorschau erscheint, sobald alle Pflichtangaben gültig sind"
            + (f" ({len(open_fields)} Felder offen oder fehlerhaft)." if open_fields else ".")
        )
    elif saved is None or vd.content_key(saved.assumptions) != vd.content_key(built.assumptions):
        st.info("Nicht gespeicherte Änderungen: Die Vorschau unten ist noch nicht gespeichert.")
    else:
        st.caption(
            f"Gespeichert: Revision {doc.revision}, Annahmenversion {saved.assumptions.version}."
        )
    if built.assumptions is not None:
        gaps = vd.blocking_texts(built.assumptions.issues_for(doc.effective_report_set()))
        if gaps:
            st.caption(
                "Beim Speichern in diesem Stand liefert das Szenario in der Übersicht keinen Wert, weil:"
            )
            _lines(gaps)
    can_save = editable and ctx.source.scenario_saver is not None
    if not can_save:
        st.caption("Speichern ist nicht möglich (nicht eingerichtet oder neuer Berichtssatz noch nicht übernommen).")
    if st.button(
        f"Szenario „{chosen}“ speichern",
        key=SAVE_KEY,
        disabled=not can_save,
        on_click=_mark_attempted,
        args=(scenario,),
    ):
        if built.assumptions is None:
            st.error("Nicht gespeichert: Bitte die markierten Felder korrigieren. Es wurden keine Teilwerte gespeichert.")
        else:
            _commit(
                ctx,
                vd.save_scenario(doc, built.assumptions, ctx.now),
                f"Szenario „{chosen}“ gespeichert",
            )
    return scenario, built.assumptions, saved


def _result_metrics(result, currency: str, label: str) -> None:
    columns = st.columns(3)
    with columns[0]:
        st.metric(f"Wert je Aktie ({label})", vd.format_per_share(result.value_per_share, currency))
    with columns[1]:
        st.metric("Zielpreis", vd.format_per_share(result.target_price, currency))
    with columns[2]:
        st.metric(
            "Terminalwertanteil",
            vd.format_percent(result.terminal_value_share),
        )


def _render_calculation(
    ctx: DetailContext,
    doc: ScenarioDocument,
    scenario: Scenario,
    draft,
    saved,
) -> None:
    from portfolio_dashboard.dcf import calculate_scenario

    currency = doc.originals.currency
    st.subheader("Rechenweg")
    if draft is not None:
        assumptions, basis = draft, "Vorschau (nicht gespeichert, rechnerisch als bestätigt behandelt)"
        calc = vd.preview_calculation(doc, draft)
    elif saved is not None:
        assumptions, basis = saved.assumptions, "gespeicherter Stand (der Entwurf ist ungültig)"
        calc = calculate_scenario(doc.effective_report_set(), saved.assumptions)
    else:
        st.caption(
            "Der Rechenweg erscheint, sobald ein Szenario vollständig und gültig eingetragen ist."
        )
        return
    st.caption(
        f"Szenario „{vd.SCENARIO_LABELS[scenario]}“ – {basis}. Alle Beträge in Mio. {currency}, "
        "ungerundet gerechnet und nur für die Anzeige gerundet."
    )
    result = calc.result
    blocking = vd.blocking_texts(result.issues)
    if blocking:
        st.warning("In diesem Stand ergibt sich kein Wert je Aktie, weil:")
        _lines(blocking)
    _result_metrics(result, currency, vd.SCENARIO_LABELS[scenario])
    path = vd.calculation_path(assumptions, result, calc.limitations)
    st.markdown("**1. FCFF-Prognose und Barwerte**")
    _table(path.years)
    st.markdown("**2. Restwert und operativer Wert**")
    _table(path.terminal)
    st.markdown(f"**Terminalwertanteil** am operativen Wert: {path.terminal_share}")
    if path.dominance_hint:
        st.warning(path.dominance_hint)
    st.markdown("**3. Eigenkapitalbrücke**")
    _table(path.bridge)
    st.markdown("**4. Aktienzahl und Wert je Aktie**")
    _table(path.value_lines)
    st.caption(
        "Der Zielpreis ist ein persönlicher Abschlag auf den Modellwert; er verändert den DCF-Wert nicht "
        "und ist keine Empfehlung."
    )
    if saved is not None:
        stored = calculate_scenario(doc.effective_report_set(), saved.assumptions)
        extra = vd.blocking_texts(doc.issues_for(scenario))
        st.caption(
            f"Gespeichert (Revision {doc.revision}): Wert je Aktie "
            f"{vd.format_per_share(stored.result.value_per_share if not extra else None, currency)}"
            + ("" if stored.result.value_per_share is not None and not extra else " (gesperrt: siehe Hinweise)")
        )
    st.session_state["valuation.limitations"] = calc.limitations
    _render_sensitivity(doc, assumptions)


def _render_sensitivity(doc: ScenarioDocument, assumptions) -> None:
    st.subheader("Sensitivität: WACC und Wachstum")
    st.caption(
        "Wert je Aktie bei abweichendem WACC (Zeilen) und Wachstum g (Spalten) um die Werte des "
        "Szenarios (● = das Szenario selbst). Jede Zelle wird einschließlich der nötigen Reinvestition "
        "neu gerechnet. Die Schrittweiten sind reine Anzeigeparameter, keine Annahmen."
    )
    columns = st.columns(2)
    with columns[0]:
        wacc_step = st.selectbox(
            STEP_WACC_LABEL,
            vd.WACC_STEPS,
            index=1,
            format_func=lambda s: f"{vd.format_percent(s)} Punkte",
            key=SENS_WACC_KEY,
        )
    with columns[1]:
        growth_step = st.selectbox(
            STEP_GROWTH_LABEL,
            vd.GROWTH_STEPS,
            index=1,
            format_func=lambda s: f"{vd.format_percent(s)} Punkte",
            key=SENS_GROWTH_KEY,
        )
    view = vd.sensitivity_view(
        doc.effective_report_set(), vd.preview_assumptions(assumptions), wacc_step, growth_step
    )
    if view.table is None:
        st.warning("Die Sensitivität ist nicht berechenbar, weil:")
        _lines(view.blocked)
        return
    _table(view.table)
    if view.notes:
        st.caption("Nicht berechenbare Kombinationen (n. v.):")
        _lines(view.notes)
    st.caption(f"Währung: {doc.originals.currency} je Aktie.")


def _render_limits(limitations) -> None:
    st.subheader("Modellgrenzen")
    texts = list(vd.limitation_texts(limitations or ()))
    texts.append(
        "Die Spanne zwischen vorsichtigem und optimistischem Szenario zeigt nur, wie stark die Annahmen "
        "wirken; sie ist keine sichere Prognose und kein Konfidenzintervall."
    )
    texts.append(
        "Analystenziele und Marktpreise fließen nicht in den inneren Wert ein; es gibt kein Kauf- oder "
        "Verkaufsurteil."
    )
    _lines(dict.fromkeys(texts))


def _render_adopt(ctx: DetailContext, doc: ScenarioDocument) -> None:
    report_set = ctx.entry.report_set
    st.warning(
        "Neuer Bericht: Die gespeicherten Annahmen und manuellen Ergänzungen gehören zu einem früheren "
        "Berichtssatz und werden nicht auf die neuen Zahlen angewendet. Übernehmen Sie den neuen "
        "Berichtssatz; danach sind Ergänzungen und Annahmen als prüfbedürftig markiert und müssen "
        "ausdrücklich neu geprüft und gespeichert werden. Frühere Revisionen bleiben erhalten."
    )
    if ctx.source.scenario_saver is not None and st.button(
        "Neuen Berichtssatz übernehmen", key=ADOPT_KEY
    ):
        _commit(
            ctx,
            vd.adopt_report_set(doc, report_set, ctx.now),
            "Neuer Berichtssatz übernommen; Ergänzungen und Annahmen sind zu prüfen",
        )


# --- entry point ---------------------------------------------------------------------


def render_detail(ctx: DetailContext) -> None:
    """Draw the detail view of one security (the overview run supplies all inputs)."""
    _retain_drafts()
    row = ctx.row
    if st.button("Zurück zur Übersicht", key=BACK_KEY):
        clear_drafts()
        ctx.on_close()
        st.rerun()
    st.subheader(f"Details und Annahmen: {markdown_text(row.name)}")
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        st.success(notice)
    st.caption(INTRO_TEXT)
    if ctx.source.demo:
        st.info(DEMO_TEXT)
    conflict = st.session_state.get(CONFLICT_KEY)
    if conflict:
        st.error(
            f"{conflict} Ein anderer Stand wurde zwischenzeitlich gespeichert. Ihre Eingaben bleiben "
            "bis zum Neuladen sichtbar (zum Sichern kopieren); das Neuladen verwirft sie."
        )
        if st.button("Gespeicherten Stand neu laden", key="valuation.conflict_reload"):
            clear_drafts()
            st.session_state[RELOAD_KEY] = True
            ctx.on_saved()
            st.rerun()
    _render_summary(ctx)
    entry = ctx.entry
    if entry is None or entry.report_set is None:
        st.warning(
            "Für diese Aktie liegen keine verwertbaren Fundamentaldaten vor; ohne sie gibt es weder "
            "Geschäftszahlen noch einen Editor."
        )
        _lines(row.reasons)
        return
    stale = (
        ctx.document is not None
        and ctx.document.originals.input_version != entry.report_set.input_version
    )
    if stale:
        _render_adopt(ctx, ctx.document)
        _render_history(vd.new_document(entry.report_set, ctx.now))
        return
    doc = ctx.document or vd.new_document(entry.report_set, ctx.now)
    _seed(doc)
    if ctx.document is None:
        st.info(
            "Für diese Aktie ist noch nichts gespeichert. Ergänzungen und Szenarien werden beim ersten "
            "Speichern als Revision 1 angelegt."
        )
    editable = ctx.source.scenario_saver is not None
    _render_open_points(doc, ctx.now)
    _render_history(doc)
    _render_manual(ctx, doc, editable)
    scenario, draft, saved = _render_editor(ctx, doc, editable)
    _render_calculation(ctx, doc, scenario, draft, saved)
    _render_limits(st.session_state.pop("valuation.limitations", ()))


__all__ = ["DetailContext", "render_detail", "clear_drafts", "Decimal"]
